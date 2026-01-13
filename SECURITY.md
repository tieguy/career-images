# Security Documentation

This document describes the security measures implemented in the Wikipedia Career Image Diversity Tool and remaining security considerations for deployment.

## Security Measures Implemented

### 1. Authentication & Session Security

- **Flask SECRET_KEY**: Sessions are signed with a cryptographically secure key
  - Set via `FLASK_SECRET_KEY` environment variable in production
  - Auto-generates secure random key if not set (for development only)
- **Session Cookie Hardening**:
  - `HttpOnly`: Prevents JavaScript access to session cookies
  - `SameSite=Lax`: Mitigates CSRF for navigation requests
  - `Secure`: Enforced in production (requires HTTPS)

### 2. CSRF Protection

All POST endpoints are protected with CSRF tokens:
- Tokens are generated per-session and validated on all form submissions
- Forms include hidden `_csrf_token` field
- Requests without valid tokens receive HTTP 403

Protected endpoints:
- `/career/<id>/update` - Status updates
- `/career/<id>/select-image` - Image selection
- `/quick-review/<id>/status` - Quick review status

### 3. Input Validation

- **Wikidata IDs**: Validated against pattern `^Q\d+$` (e.g., Q42)
- **Openverse Image IDs**: Validated as proper UUIDs
- **URLs**: Validated to ensure `http://` or `https://` schemes only
  - Prevents `javascript:`, `data:`, and other dangerous schemes
- **Text Fields**: Length-limited to prevent DoS:
  - Notes: 2000 characters
  - Creator: 200 characters
  - Caption: 500 characters
  - Search queries: 200 characters

### 4. XSS Prevention

- **Jinja2 Auto-escaping**: Enabled by default for all templates
- **JavaScript DOM Manipulation**: Replaced unsafe `innerHTML` with safe DOM methods
  - All API response data is escaped before rendering
  - URL validation before setting `href` attributes
- **Content Security Policy**: Restricts script and resource loading

### 5. SQL Injection Prevention

- **Parameterized Queries**: All database queries use parameter binding
- **LIKE Wildcard Escaping**: Search queries escape `%` and `_` wildcards
  - Prevents attackers from using `%` to match all records

### 6. Security Headers

All responses include:
- `X-Frame-Options: DENY` - Prevents clickjacking
- `X-Content-Type-Options: nosniff` - Prevents MIME sniffing
- `X-XSS-Protection: 1; mode=block` - Legacy XSS filter
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy`:
  - `default-src 'self'`
  - `script-src 'self' 'unsafe-inline'` (needed for inline scripts)
  - `img-src` allows Wikipedia, Wikimedia, Openverse, Flickr
  - `frame-ancestors 'none'` - Additional clickjacking protection
  - `form-action 'self'` - Forms can only submit to same origin

### 7. Rate Limiting

Simple in-memory rate limiting protects API endpoints:
- `/api/stats`: 60 requests/minute
- `/api/openverse/search`: 30 requests/minute
- `/api/openverse/image/<id>`: 60 requests/minute

### 8. Debug Mode Disabled by Default

Flask debug mode is OFF by default (previously defaulted to ON).
- Set `FLASK_DEBUG=1` explicitly for development
- Debug mode exposes interactive debugger with code execution

## Deployment Checklist

Before deploying to production:

1. **Set Environment Variables**:
   ```bash
   export FLASK_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
   export FLASK_ENV=production
   export FLASK_DEBUG=0
   ```

2. **Use HTTPS**: Required for secure cookies and to prevent MITM attacks

3. **Configure Reverse Proxy** (nginx/Apache):
   - Forward `X-Forwarded-For` header for accurate rate limiting
   - Add additional rate limiting at proxy level
   - Enable HTTPS with valid certificates

4. **Database Security** (Toolforge):
   - Ensure `~/replica.my.cnf` has restrictive permissions (600)
   - Database credentials are read from this file, not hardcoded

## Remaining Security Considerations

### Known Limitations

1. **In-Memory Rate Limiting**:
   - Not shared across workers/processes
   - Resets on application restart
   - For production, consider Redis-based solution

2. **CSP `unsafe-inline`**:
   - Required for inline scripts in templates
   - Could be improved by moving scripts to external files with nonces

3. **No Authentication**:
   - App is designed for open access
   - Anyone can update career statuses
   - Consider adding authentication for Toolforge deployment if abuse occurs

4. **External API Trust**:
   - Wikipedia/Wikidata APIs are trusted sources
   - Openverse API responses are sanitized but could contain unexpected data
   - Content from these APIs is displayed to users

### Potential Attack Vectors

1. **Denial of Service**:
   - Rate limiting provides basic protection
   - Large-scale attacks need infrastructure-level mitigation
   - Consider Toolforge's built-in protections

2. **Data Integrity**:
   - No authentication means anyone can modify review statuses
   - Audit trail exists (reviewed_by, reviewed_at fields)
   - Consider periodic backups

3. **Third-Party Dependencies**:
   - Keep dependencies updated (`uv update`)
   - Review security advisories for Flask, requests, aiohttp

### Monitoring Recommendations

1. **Log suspicious activity**:
   - Failed CSRF validations
   - Rate limit hits
   - Invalid input rejections

2. **Monitor for abuse patterns**:
   - Mass status changes
   - Unusual traffic spikes
   - Repeated failed requests

## Security Contact

Report security issues to the project maintainer via the GitHub repository's security advisories or private disclosure methods.
