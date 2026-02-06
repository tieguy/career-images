"""
Tests for commons.py - Wikimedia Commons API integration.
"""

import pytest
import responses
from commons import (
    fetch_category_members,
    fetch_subcategories,
    fetch_category_info,
    fetch_category_files,
    _parse_file_pages,
    _category_url,
)


API_URL = 'https://commons.wikimedia.org/w/api.php'


class TestFetchCategoryMembers:
    """Tests for fetching files from a Commons category."""

    @responses.activate
    def test_returns_files(self):
        responses.add(
            responses.GET, API_URL,
            json={
                'query': {
                    'pages': {
                        '12345': {
                            'pageid': 12345,
                            'title': 'File:Test_image.jpg',
                            'imageinfo': [{
                                'url': 'https://upload.wikimedia.org/commons/a/ab/Test_image.jpg',
                                'thumburl': 'https://upload.wikimedia.org/thumb/Test_image.jpg/300px-Test_image.jpg',
                                'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Test_image.jpg',
                                'extmetadata': {
                                    'ImageDescription': {'value': 'A test image'}
                                },
                            }],
                        }
                    }
                }
            },
            status=200,
        )

        result = fetch_category_members('Test category')
        assert result['category'] == 'Test category'
        assert len(result['files']) == 1
        assert result['files'][0]['title'] == 'File:Test_image.jpg'
        assert result['files'][0]['description'] == 'A test image'

    @responses.activate
    def test_returns_continuation_token(self):
        responses.add(
            responses.GET, API_URL,
            json={
                'continue': {'gcmcontinue': 'page|token|123'},
                'query': {
                    'pages': {
                        '1': {
                            'pageid': 1,
                            'title': 'File:A.jpg',
                            'imageinfo': [{'url': 'https://example.com/a.jpg'}],
                        }
                    }
                }
            },
            status=200,
        )

        result = fetch_category_members('Big category')
        assert result['continue_from'] == 'page|token|123'

    @responses.activate
    def test_no_continuation_when_complete(self):
        responses.add(
            responses.GET, API_URL,
            json={
                'query': {
                    'pages': {
                        '1': {
                            'pageid': 1,
                            'title': 'File:A.jpg',
                            'imageinfo': [{'url': 'https://example.com/a.jpg'}],
                        }
                    }
                }
            },
            status=200,
        )

        result = fetch_category_members('Small category')
        assert result['continue_from'] is None

    @responses.activate
    def test_handles_empty_category(self):
        responses.add(
            responses.GET, API_URL,
            json={'batchcomplete': ''},
            status=200,
        )

        result = fetch_category_members('Empty category')
        assert result['files'] == []

    @responses.activate
    def test_handles_api_error(self):
        responses.add(
            responses.GET, API_URL,
            json={'error': {'code': 'internal_api_error'}},
            status=500,
        )

        result = fetch_category_members('Error category')
        assert result['files'] == []
        assert 'error' in result

    @responses.activate
    def test_handles_timeout(self):
        import requests as req
        responses.add(
            responses.GET, API_URL,
            body=req.exceptions.Timeout(),
        )

        result = fetch_category_members('Timeout category')
        assert result['files'] == []
        assert 'error' in result

    @responses.activate
    def test_strips_html_from_descriptions(self):
        responses.add(
            responses.GET, API_URL,
            json={
                'query': {
                    'pages': {
                        '1': {
                            'pageid': 1,
                            'title': 'File:A.jpg',
                            'imageinfo': [{
                                'url': 'https://example.com/a.jpg',
                                'extmetadata': {
                                    'ImageDescription': {'value': '<b>Bold</b> and <a href="#">link</a>'}
                                },
                            }],
                        }
                    }
                }
            },
            status=200,
        )

        result = fetch_category_members('HTML category')
        assert '<' not in result['files'][0]['description']
        assert 'Bold' in result['files'][0]['description']


class TestFetchSubcategories:
    """Tests for fetching subcategories."""

    @responses.activate
    def test_returns_subcategories(self):
        responses.add(
            responses.GET, API_URL,
            json={
                'query': {
                    'categorymembers': [
                        {'title': 'Category:DJs by nationality'},
                        {'title': 'Category:Female DJs'},
                    ]
                }
            },
            status=200,
        )

        subcats = fetch_subcategories('Disc jockeys')
        assert len(subcats) == 2
        assert subcats[0]['name'] == 'DJs by nationality'
        assert subcats[0]['title'] == 'Category:DJs by nationality'
        assert 'commons.wikimedia.org' in subcats[0]['url']

    @responses.activate
    def test_empty_subcategories(self):
        responses.add(
            responses.GET, API_URL,
            json={'query': {'categorymembers': []}},
            status=200,
        )

        subcats = fetch_subcategories('Leaf category')
        assert subcats == []

    @responses.activate
    def test_handles_api_error(self):
        responses.add(
            responses.GET, API_URL,
            json={'error': 'fail'},
            status=500,
        )

        subcats = fetch_subcategories('Error category')
        assert subcats == []


class TestFetchCategoryInfo:
    """Tests for fetching category statistics."""

    @responses.activate
    def test_returns_counts(self):
        responses.add(
            responses.GET, API_URL,
            json={
                'query': {
                    'pages': {
                        '123': {
                            'categoryinfo': {
                                'files': 42,
                                'subcats': 5,
                                'pages': 50,
                            }
                        }
                    }
                }
            },
            status=200,
        )

        info = fetch_category_info('Test')
        assert info['files'] == 42
        assert info['subcategories'] == 5
        assert info['pages'] == 50

    @responses.activate
    def test_handles_missing_categoryinfo(self):
        responses.add(
            responses.GET, API_URL,
            json={'query': {'pages': {'123': {}}}},
            status=200,
        )

        info = fetch_category_info('No info')
        assert info['files'] == 0
        assert info['subcategories'] == 0

    @responses.activate
    def test_handles_api_error(self):
        responses.add(
            responses.GET, API_URL,
            json={'error': 'fail'},
            status=500,
        )

        info = fetch_category_info('Error')
        assert info['files'] == 0


class TestParseFilePages:
    """Tests for the internal _parse_file_pages helper."""

    def test_parses_valid_pages(self):
        pages = {
            '100': {
                'pageid': 100,
                'title': 'File:Photo.jpg',
                'imageinfo': [{
                    'url': 'https://example.com/photo.jpg',
                    'thumburl': 'https://example.com/thumb.jpg',
                    'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Photo.jpg',
                    'extmetadata': {},
                }],
            }
        }
        result = _parse_file_pages(pages)
        assert len(result) == 1
        assert result[0]['title'] == 'File:Photo.jpg'
        assert result[0]['image_url'] == 'https://example.com/photo.jpg'

    def test_skips_negative_page_ids(self):
        pages = {
            '-1': {'title': 'Missing', 'imageinfo': [{'url': 'https://example.com/x.jpg'}]},
        }
        result = _parse_file_pages(pages)
        assert result == []

    def test_skips_pages_without_url(self):
        pages = {
            '100': {'pageid': 100, 'title': 'File:NoUrl.jpg', 'imageinfo': [{}]},
        }
        result = _parse_file_pages(pages)
        assert result == []

    def test_empty_pages(self):
        assert _parse_file_pages({}) == []


class TestCategoryUrl:
    """Tests for category URL generation."""

    def test_basic_category(self):
        url = _category_url('Disc jockeys')
        assert url == 'https://commons.wikimedia.org/wiki/Category:Disc_jockeys'

    def test_category_with_special_chars(self):
        url = _category_url('Musicians from New York (state)')
        assert 'Category:Musicians_from_New_York_' in url


class TestFetchCategoryFilesCompat:
    """Test the backwards-compatible wrapper."""

    @responses.activate
    def test_delegates_to_fetch_category_members(self):
        responses.add(
            responses.GET, API_URL,
            json={
                'query': {
                    'pages': {
                        '1': {
                            'pageid': 1,
                            'title': 'File:A.jpg',
                            'imageinfo': [{'url': 'https://example.com/a.jpg'}],
                        }
                    }
                }
            },
            status=200,
        )

        result = fetch_category_files('Test')
        assert 'files' in result
        assert 'category' in result
