"""
Tests for app.py - Flask routes and input validation.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import (
    app,
    is_valid_wikidata_id,
    is_valid_url,
    sanitize_search_query,
    RateLimiter,
)


# =============================================================================
# Input validation tests
# =============================================================================

class TestIsValidWikidataId:
    """Tests for Wikidata ID validation."""

    def test_valid_id(self):
        assert is_valid_wikidata_id('Q42') is True
        assert is_valid_wikidata_id('Q123456') is True

    def test_invalid_missing_q(self):
        assert is_valid_wikidata_id('42') is False
        assert is_valid_wikidata_id('P106') is False

    def test_invalid_with_letters(self):
        assert is_valid_wikidata_id('Q12abc') is False

    def test_empty_and_none(self):
        assert is_valid_wikidata_id('') is False
        assert is_valid_wikidata_id(None) is False

    def test_injection_attempt(self):
        assert is_valid_wikidata_id("Q1'; DROP TABLE careers;--") is False
        assert is_valid_wikidata_id('Q1/../../etc/passwd') is False


class TestIsValidUrl:
    """Tests for URL validation."""

    def test_valid_https(self):
        assert is_valid_url('https://example.com/image.jpg') is True

    def test_valid_http(self):
        assert is_valid_url('http://example.com') is True

    def test_invalid_javascript(self):
        assert is_valid_url('javascript:alert(1)') is False

    def test_invalid_data_uri(self):
        assert is_valid_url('data:text/html,<h1>XSS</h1>') is False

    def test_empty(self):
        assert is_valid_url('') is False
        assert is_valid_url(None) is False

    def test_not_a_url(self):
        assert is_valid_url('not a url') is False


class TestSanitizeSearchQuery:
    """Tests for search query sanitization."""

    def test_normal_query(self):
        assert sanitize_search_query('software engineer') == 'software engineer'

    def test_escapes_percent(self):
        result = sanitize_search_query('100%')
        assert '%' not in result or '\\%' in result

    def test_escapes_underscore(self):
        result = sanitize_search_query('some_thing')
        assert '\\_' in result

    def test_truncates_long_queries(self):
        long_query = 'a' * 500
        result = sanitize_search_query(long_query)
        assert len(result) <= 200

    def test_empty_query(self):
        assert sanitize_search_query('') == ''
        assert sanitize_search_query(None) == ''


# =============================================================================
# Rate limiter tests
# =============================================================================

class TestRateLimiter:
    """Tests for the rate limiter."""

    def test_allows_requests_under_limit(self):
        limiter = RateLimiter(requests_per_minute=5)
        for _ in range(5):
            assert limiter.is_allowed('test-ip') is True

    def test_blocks_requests_over_limit(self):
        limiter = RateLimiter(requests_per_minute=3)
        for _ in range(3):
            limiter.is_allowed('test-ip')

        assert limiter.is_allowed('test-ip') is False

    def test_different_keys_are_independent(self):
        limiter = RateLimiter(requests_per_minute=1)
        assert limiter.is_allowed('ip-1') is True
        assert limiter.is_allowed('ip-2') is True  # Different key, should pass
        assert limiter.is_allowed('ip-1') is False  # Same key, should fail

    def test_cleanup_removes_stale_entries(self):
        limiter = RateLimiter(requests_per_minute=5)
        limiter.is_allowed('old-ip')
        # Manually age the timestamp
        limiter.requests['old-ip'] = [0]  # epoch time = very old
        limiter.cleanup()
        assert 'old-ip' not in limiter.requests


# =============================================================================
# Route tests
# =============================================================================

@pytest.fixture
def client():
    """Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


class TestHealthCheck:
    """Tests for the health check endpoint."""

    def test_health_check(self, client):
        response = client.get('/healthz')
        assert response.status_code == 200
        assert response.data == b'OK'


class TestIndexRoute:
    """Tests for the career list page."""

    def test_index_loads(self, client):
        response = client.get('/')
        assert response.status_code == 200
        assert b'Career' in response.data

    def test_index_with_status_filter(self, client):
        response = client.get('/?status=unreviewed')
        assert response.status_code == 200

    def test_index_with_search(self, client):
        response = client.get('/?q=doctor')
        assert response.status_code == 200

    def test_index_pagination(self, client):
        response = client.get('/?page=2')
        assert response.status_code == 200


class TestCareerDetailRoute:
    """Tests for the career detail page."""

    def test_invalid_wikidata_id_rejected(self, client):
        response = client.get('/career/INVALID')
        assert response.status_code == 400

    def test_sql_injection_rejected(self, client):
        response = client.get("/career/Q1'%20OR%201=1--")
        assert response.status_code == 400

    def test_nonexistent_career_404(self, client):
        response = client.get('/career/Q999999999')
        assert response.status_code == 404


class TestCommonsRoutes:
    """Tests for Commons review routes."""

    def test_commons_index_loads(self, client):
        response = client.get('/commons')
        assert response.status_code == 200

    def test_commons_review_invalid_id(self, client):
        response = client.get('/commons/INVALID')
        assert response.status_code == 400

    def test_commons_review_nonexistent(self, client):
        response = client.get('/commons/Q999999999')
        assert response.status_code == 404


class TestApiRoutes:
    """Tests for API endpoints."""

    def test_stats_endpoint(self, client):
        response = client.get('/api/stats')
        assert response.status_code == 200
        data = response.get_json()
        assert 'total_careers' in data

    def test_openverse_search_requires_query(self, client):
        response = client.get('/api/openverse/search')
        assert response.status_code == 400

    def test_openverse_search_rejects_long_query(self, client):
        response = client.get('/api/openverse/search?q=' + 'a' * 300)
        assert response.status_code == 400

    def test_openverse_search_rejects_invalid_page(self, client):
        response = client.get('/api/openverse/search?q=test&page=999')
        assert response.status_code == 400

    def test_openverse_image_rejects_invalid_id(self, client):
        response = client.get('/api/openverse/image/not-a-uuid')
        assert response.status_code == 400

    def test_commons_category_files_requires_category(self, client):
        response = client.get('/api/commons/category-files')
        assert response.status_code == 400

    def test_commons_category_files_rejects_invalid_chars(self, client):
        response = client.get('/api/commons/category-files?category=<script>')
        assert response.status_code == 400


class TestCsrfProtection:
    """Tests for CSRF token validation."""

    def test_post_without_csrf_is_rejected(self, client):
        response = client.post('/career/Q42/update', data={'status': 'unreviewed'})
        assert response.status_code == 403

    def test_commons_post_without_csrf_is_rejected(self, client):
        response = client.post('/commons/Q42/update', data={'commons_status': 'unreviewed'})
        assert response.status_code == 403


class TestSecurityHeaders:
    """Tests for security response headers."""

    def test_security_headers_present(self, client):
        response = client.get('/healthz')
        assert response.headers.get('X-Frame-Options') == 'DENY'
        assert response.headers.get('X-Content-Type-Options') == 'nosniff'
        assert response.headers.get('X-XSS-Protection') == '1; mode=block'
        assert 'strict-origin' in response.headers.get('Referrer-Policy', '')
        assert 'Content-Security-Policy' in response.headers

    def test_csp_blocks_object(self, client):
        response = client.get('/healthz')
        csp = response.headers.get('Content-Security-Policy', '')
        assert "object-src 'none'" in csp

    def test_csp_blocks_framing(self, client):
        response = client.get('/healthz')
        csp = response.headers.get('Content-Security-Policy', '')
        assert "frame-ancestors 'none'" in csp
