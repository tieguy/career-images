"""
app.py - Flask web application for Wikipedia career image diversity review
"""

import os
import re
import secrets
import hashlib
import hmac
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, session, abort
from db import get_database, VALID_STATUSES
from wikipedia import fetch_career_data
from openverse import search_images, get_image_detail, generate_attribution

app = Flask(__name__)

# SECURITY: Secret key for session signing and CSRF tokens
# In production, set FLASK_SECRET_KEY environment variable to a secure random value
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32)

# Session cookie security settings
app.config.update(
    SESSION_COOKIE_SECURE=os.environ.get('FLASK_ENV') == 'production',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)


# =============================================================================
# SECURITY: Input Validation
# =============================================================================

# Wikidata ID format: Q followed by digits (e.g., Q42, Q123456)
WIKIDATA_ID_PATTERN = re.compile(r'^Q\d+$')

# Openverse image ID format: UUID
UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


def is_valid_wikidata_id(wikidata_id: str) -> bool:
    """Validate that a string is a valid Wikidata Q-ID."""
    return bool(wikidata_id and WIKIDATA_ID_PATTERN.match(wikidata_id))


def is_valid_url(url: str, allowed_schemes: tuple = ('http', 'https')) -> bool:
    """Validate that a string is a valid URL with allowed scheme."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        return parsed.scheme in allowed_schemes and bool(parsed.netloc)
    except Exception:
        return False


def sanitize_search_query(query: str) -> str:
    """Sanitize search query - escape SQL LIKE wildcards and limit length."""
    if not query:
        return ''
    # Limit length to prevent DoS
    query = query[:200]
    # Escape SQL LIKE wildcards (% and _) to prevent wildcard injection
    query = query.replace('%', '\\%').replace('_', '\\_')
    return query


# =============================================================================
# SECURITY: CSRF Protection
# =============================================================================

def generate_csrf_token():
    """Generate a CSRF token for the current session."""
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']


def validate_csrf_token():
    """Validate the CSRF token from the form matches the session."""
    token = request.form.get('_csrf_token')
    session_token = session.get('_csrf_token')
    if not token or not session_token:
        return False
    return hmac.compare_digest(token, session_token)


def csrf_protect(f):
    """Decorator to require valid CSRF token for POST requests."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method == 'POST':
            if not validate_csrf_token():
                abort(403, description="CSRF token validation failed")
        return f(*args, **kwargs)
    return decorated_function


# Make CSRF token available in all templates
app.jinja_env.globals['csrf_token'] = generate_csrf_token


# =============================================================================
# SECURITY: Rate Limiting (simple in-memory implementation)
# =============================================================================

from collections import defaultdict
import time
import threading

class RateLimiter:
    """Simple in-memory rate limiter. For production, consider Redis-based solution."""

    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.window_size = 60  # seconds
        self.requests = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        """Check if request is allowed for the given key (e.g., IP address)."""
        now = time.time()
        window_start = now - self.window_size

        with self._lock:
            # Clean old entries
            self.requests[key] = [t for t in self.requests[key] if t > window_start]

            if len(self.requests[key]) >= self.requests_per_minute:
                return False

            self.requests[key].append(now)
            return True

    def cleanup(self):
        """Remove stale entries to prevent memory growth."""
        now = time.time()
        window_start = now - self.window_size
        with self._lock:
            keys_to_remove = []
            for key, timestamps in self.requests.items():
                self.requests[key] = [t for t in timestamps if t > window_start]
                if not self.requests[key]:
                    keys_to_remove.append(key)
            for key in keys_to_remove:
                del self.requests[key]


# Rate limiters for different endpoints
api_rate_limiter = RateLimiter(requests_per_minute=60)  # 60 requests/min for API
search_rate_limiter = RateLimiter(requests_per_minute=30)  # 30 searches/min


def get_client_ip():
    """Get client IP address, handling proxies."""
    # Check for X-Forwarded-For header (common with reverse proxies)
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or '127.0.0.1'


def rate_limit(limiter: RateLimiter):
    """Decorator to apply rate limiting to a route."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            client_ip = get_client_ip()
            if not limiter.is_allowed(client_ip):
                return jsonify({'error': 'Rate limit exceeded. Please try again later.'}), 429
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# =============================================================================
# SECURITY: Response Headers
# =============================================================================

@app.after_request
def add_security_headers(response):
    """Add security headers to all responses."""
    # Prevent clickjacking
    response.headers['X-Frame-Options'] = 'DENY'
    # Prevent MIME type sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # XSS protection (legacy browsers)
    response.headers['X-XSS-Protection'] = '1; mode=block'
    # Referrer policy
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # Content Security Policy - restrict script sources
    # Note: 'unsafe-inline' needed for inline scripts; consider moving to external files
    csp = (
        "default-src 'self'; "
        "img-src 'self' https://*.wikimedia.org https://*.wikipedia.org https://api.openverse.org https://*.openverse.org https://live.staticflickr.com https://*.staticflickr.com data: https:; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "base-uri 'self';"
    )
    response.headers['Content-Security-Policy'] = csp
    return response

# Get database instance
db = get_database()
db.init_schema()


@app.route('/healthz')
def health_check():
    """Health check endpoint for Toolforge"""
    return 'OK', 200


@app.route('/sw.js')
def service_worker():
    """Serve service worker from root for proper scope"""
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')


@app.route('/')
def index():
    """Career list view - ranked by pageviews"""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    status_filter = request.args.get('status', '')
    search_query = request.args.get('q', '').strip()

    # Get careers
    if search_query:
        careers = db.search_careers(search_query, limit=500)
    elif status_filter and status_filter in VALID_STATUSES:
        careers = db.get_careers_by_status(status_filter, limit=1000)
    else:
        careers = db.get_all_careers()

    # Paginate
    total = len(careers)
    start = (page - 1) * per_page
    end = start + per_page
    careers_page = careers[start:end]

    # Add rank numbers
    for i, career in enumerate(careers_page):
        career['rank'] = start + i + 1

    stats = db.get_stats()

    return render_template('index.html',
                           careers=careers_page,
                           page=page,
                           per_page=per_page,
                           total=total,
                           total_pages=(total + per_page - 1) // per_page,
                           status_filter=status_filter,
                           search_query=search_query,
                           stats=stats)


@app.route('/career/<wikidata_id>')
def career_detail(wikidata_id):
    """Detail view for a single career"""
    # SECURITY: Validate wikidata_id format to prevent injection
    if not is_valid_wikidata_id(wikidata_id):
        abort(400, description="Invalid career ID format")

    career = db.get_career(wikidata_id)
    if not career:
        return "Career not found", 404

    # Fetch Wikipedia data on-demand
    wiki_data = fetch_career_data(career['wikipedia_url'])

    # Get stored images (if any)
    stored_images = db.get_career_images(wikidata_id)

    # Get previous/next career for navigation
    all_careers = db.get_all_careers()
    current_idx = None
    for i, c in enumerate(all_careers):
        if c['wikidata_id'] == wikidata_id:
            current_idx = i
            break

    prev_career = all_careers[current_idx - 1] if current_idx and current_idx > 0 else None
    next_career = all_careers[current_idx + 1] if current_idx is not None and current_idx < len(all_careers) - 1 else None

    return render_template('career_detail.html',
                           career=career,
                           wiki_data=wiki_data,
                           stored_images=stored_images,
                           prev_career=prev_career,
                           next_career=next_career,
                           valid_statuses=VALID_STATUSES)


@app.route('/career/<wikidata_id>/update', methods=['POST'])
@csrf_protect
def update_career(wikidata_id):
    """Update career status"""
    # SECURITY: Validate wikidata_id format
    if not is_valid_wikidata_id(wikidata_id):
        abort(400, description="Invalid career ID format")

    status = request.form.get('status')
    # SECURITY: Limit and sanitize notes field
    notes = request.form.get('notes', '')[:2000]
    reviewed_by = request.form.get('reviewed_by', 'anonymous')[:100]

    if status and status in VALID_STATUSES:
        db.update_career_status(wikidata_id, status, reviewed_by=reviewed_by, notes=notes)

    # Check if "save and next" was clicked
    if 'save_next' in request.form:
        # Find next career
        all_careers = db.get_all_careers()
        for i, c in enumerate(all_careers):
            if c['wikidata_id'] == wikidata_id:
                if i + 1 < len(all_careers):
                    return redirect(url_for('career_detail', wikidata_id=all_careers[i + 1]['wikidata_id']))
                break

    return redirect(url_for('career_detail', wikidata_id=wikidata_id))


@app.route('/api/stats')
@rate_limit(api_rate_limiter)
def api_stats():
    """API endpoint for statistics"""
    return jsonify(db.get_stats())


@app.route('/api/openverse/search')
@rate_limit(search_rate_limiter)
def api_openverse_search():
    """Search Openverse for images"""
    query = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)

    if not query:
        return jsonify({'error': 'Missing query parameter'}), 400

    # SECURITY: Limit query length
    if len(query) > 200:
        return jsonify({'error': 'Query too long'}), 400

    # SECURITY: Limit page number to prevent abuse
    if page < 1 or page > 100:
        return jsonify({'error': 'Invalid page number'}), 400

    results = search_images(query, page=page)
    return jsonify(results)


@app.route('/api/openverse/image/<image_id>')
@rate_limit(api_rate_limiter)
def api_openverse_image(image_id):
    """Get details for a specific Openverse image"""
    # SECURITY: Validate image_id is a valid UUID format
    if not UUID_PATTERN.match(image_id):
        return jsonify({'error': 'Invalid image ID format'}), 400

    image = get_image_detail(image_id)
    if not image:
        return jsonify({'error': 'Image not found'}), 404

    image['attribution_text'] = generate_attribution(image)
    return jsonify(image)


@app.route('/quick-review')
def quick_review():
    """Quick review mode - lookup by article name"""
    article = request.args.get('article', '').strip()

    if not article:
        # Suggest a random unreviewed career
        unreviewed = db.get_careers_by_status('unreviewed', limit=100)
        import random
        random_career = random.choice(unreviewed)['name'] if unreviewed else None
        return render_template('quick_review.html', article=None, career=None, random_career=random_career)

    # Try to find by name (case-insensitive)
    careers = db.search_careers(article, limit=10)

    # Look for exact match first
    career = None
    for c in careers:
        if c['name'].lower() == article.lower():
            career = c
            break

    # If no exact match, use first result
    if not career and careers:
        career = careers[0]

    if not career:
        return render_template('quick_review.html', article=article, career=None,
                               error=f"No career found matching '{article}'")

    # Fetch Wikipedia data
    wiki_data = fetch_career_data(career['wikipedia_url'])

    return render_template('quick_review.html',
                           article=article,
                           career=career,
                           wiki_data=wiki_data,
                           valid_statuses=VALID_STATUSES)


@app.route('/quick-review/<wikidata_id>/status', methods=['POST'])
@csrf_protect
def quick_review_status(wikidata_id):
    """Update status from quick review mode"""
    # SECURITY: Validate wikidata_id format
    if not is_valid_wikidata_id(wikidata_id):
        abort(400, description="Invalid career ID format")

    status = request.form.get('status')
    # SECURITY: Limit notes field length
    notes = request.form.get('notes', '')[:2000]
    if status and status in VALID_STATUSES:
        db.update_career_status(wikidata_id, status, reviewed_by='quick-review', notes=notes)

    # Return to quick review with next unreviewed career
    careers = db.get_careers_by_status('unreviewed', limit=1)
    if careers:
        return redirect(url_for('quick_review', article=careers[0]['name']))
    return redirect(url_for('quick_review'))


@app.route('/career/<wikidata_id>/select-image', methods=['POST'])
@csrf_protect
def select_replacement_image(wikidata_id):
    """Save a selected replacement image from Openverse"""
    # SECURITY: Validate wikidata_id format
    if not is_valid_wikidata_id(wikidata_id):
        abort(400, description="Invalid career ID format")

    image_url = request.form.get('image_url', '')
    # SECURITY: Validate image URL is a valid HTTP(S) URL
    if not is_valid_url(image_url):
        abort(400, description="Invalid image URL")

    # SECURITY: Limit field lengths to prevent DoS
    caption = request.form.get('caption', '')[:500]
    creator = request.form.get('creator', '')[:200]
    license = request.form.get('license', '')[:50]
    license_url = request.form.get('license_url', '')
    source_url = request.form.get('source_url', '')

    # SECURITY: Validate optional URLs if provided
    if license_url and not is_valid_url(license_url):
        license_url = ''
    if source_url and not is_valid_url(source_url):
        source_url = ''

    db.set_replacement_image(
        wikidata_id, image_url, caption,
        creator=creator, license=license,
        license_url=license_url, source_url=source_url
    )

    return redirect(url_for('career_detail', wikidata_id=wikidata_id))


if __name__ == '__main__':
    import os
    # host=0.0.0.0 makes Flask accessible outside the container
    # SECURITY: Debug mode MUST default to OFF - it exposes interactive debugger with code execution
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug, host='0.0.0.0', port=5000)
