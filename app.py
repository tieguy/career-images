"""
app.py - Flask web application for Wikipedia career image diversity review
"""

from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory
from db import get_database, VALID_STATUSES
from wikipedia import fetch_career_data
from openverse import search_images, get_image_detail, generate_attribution

app = Flask(__name__)

# Get database instance
db = get_database()
db.init_schema()


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
def update_career(wikidata_id):
    """Update career status"""
    status = request.form.get('status')
    notes = request.form.get('notes', '')
    reviewed_by = request.form.get('reviewed_by', 'anonymous')

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
def api_stats():
    """API endpoint for statistics"""
    return jsonify(db.get_stats())


@app.route('/api/openverse/search')
def api_openverse_search():
    """Search Openverse for images"""
    query = request.args.get('q', '')
    page = request.args.get('page', 1, type=int)

    if not query:
        return jsonify({'error': 'Missing query parameter'}), 400

    results = search_images(query, page=page)
    return jsonify(results)


@app.route('/api/openverse/image/<image_id>')
def api_openverse_image(image_id):
    """Get details for a specific Openverse image"""
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
def quick_review_status(wikidata_id):
    """Update status from quick review mode"""
    status = request.form.get('status')
    if status and status in VALID_STATUSES:
        db.update_career_status(wikidata_id, status, reviewed_by='quick-review')

    # Return to quick review with next unreviewed career
    careers = db.get_careers_by_status('unreviewed', limit=1)
    if careers:
        return redirect(url_for('quick_review', article=careers[0]['name']))
    return redirect(url_for('quick_review'))


@app.route('/career/<wikidata_id>/select-image', methods=['POST'])
def select_replacement_image(wikidata_id):
    """Save a selected replacement image from Openverse"""
    image_url = request.form.get('image_url')
    caption = request.form.get('caption', '')
    creator = request.form.get('creator', '')
    license = request.form.get('license', '')
    license_url = request.form.get('license_url', '')
    source_url = request.form.get('source_url', '')

    if image_url:
        db.set_replacement_image(
            wikidata_id, image_url, caption,
            creator=creator, license=license,
            license_url=license_url, source_url=source_url
        )

    return redirect(url_for('career_detail', wikidata_id=wikidata_id))


if __name__ == '__main__':
    # host=0.0.0.0 makes Flask accessible outside the container
    app.run(debug=True, host='0.0.0.0', port=5000)
