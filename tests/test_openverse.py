"""
Tests for openverse.py - Openverse API integration.
"""

import pytest
import responses
from openverse import (
    search_images,
    get_image_detail,
    generate_attribution,
    generate_wikitext,
    is_commons_image,
    get_commons_filename,
    COMPATIBLE_LICENSES,
)


class TestSearchImages:
    """Tests for Openverse image search."""

    @responses.activate
    def test_search_returns_results(self, mock_openverse_response):
        responses.add(
            responses.GET,
            'https://api.openverse.org/v1/images/',
            json=mock_openverse_response,
            status=200,
        )

        result = search_images('software engineer')

        assert result['result_count'] == 2
        assert len(result['results']) == 2
        assert result['results'][0]['title'] == 'Female software engineer'

    @responses.activate
    def test_search_extracts_correct_fields(self, mock_openverse_response):
        responses.add(
            responses.GET,
            'https://api.openverse.org/v1/images/',
            json=mock_openverse_response,
            status=200,
        )

        result = search_images('test')
        img = result['results'][0]

        assert img['id'] == 'abc123'
        assert img['thumbnail'] == 'https://example.com/thumb1.jpg'
        assert img['url'] == 'https://example.com/image1.jpg'
        assert img['foreign_landing_url'] == 'https://flickr.com/photo/123'
        assert img['license'] == 'cc-by'
        assert img['creator'] == 'Jane Doe'
        assert img['source'] == 'flickr'

    @responses.activate
    def test_search_handles_empty_results(self):
        responses.add(
            responses.GET,
            'https://api.openverse.org/v1/images/',
            json={'results': [], 'result_count': 0, 'page_count': 0},
            status=200,
        )

        result = search_images('nonexistent query')
        assert result['result_count'] == 0
        assert len(result['results']) == 0

    @responses.activate
    def test_search_handles_api_error(self):
        responses.add(
            responses.GET,
            'https://api.openverse.org/v1/images/',
            json={'error': 'Internal Server Error'},
            status=500,
        )

        result = search_images('test')
        assert 'error' in result
        assert result['results'] == []

    @responses.activate
    def test_search_handles_timeout(self):
        import requests
        responses.add(
            responses.GET,
            'https://api.openverse.org/v1/images/',
            body=requests.exceptions.Timeout(),
        )

        result = search_images('test')
        assert 'error' in result


class TestGetImageDetail:
    """Tests for fetching individual image details."""

    @responses.activate
    def test_get_image_detail_success(self):
        image_data = {
            'id': 'abc123',
            'title': 'Test Image',
            'url': 'https://example.com/full.jpg',
            'thumbnail': 'https://example.com/thumb.jpg',
            'foreign_landing_url': 'https://flickr.com/photo/123',
            'license': 'cc-by',
            'license_url': 'https://creativecommons.org/licenses/by/4.0/',
            'license_version': '4.0',
            'creator': 'Test Creator',
            'creator_url': 'https://flickr.com/user/test',
            'source': 'flickr',
            'attribution': 'Test attribution text',
        }
        responses.add(
            responses.GET,
            'https://api.openverse.org/v1/images/abc123/',
            json=image_data,
            status=200,
        )

        result = get_image_detail('abc123')

        assert result is not None
        assert result['id'] == 'abc123'
        assert result['creator'] == 'Test Creator'
        assert result['license_version'] == '4.0'

    @responses.activate
    def test_get_image_detail_not_found(self):
        responses.add(
            responses.GET,
            'https://api.openverse.org/v1/images/notfound/',
            json={'error': 'Not found'},
            status=404,
        )

        result = get_image_detail('notfound')
        assert result is None


class TestGenerateAttribution:
    """Tests for attribution text generation."""

    def test_uses_existing_attribution(self):
        image = {'attribution': 'Existing attribution text'}
        result = generate_attribution(image)
        assert result == 'Existing attribution text'

    def test_generates_attribution_from_parts(self):
        image = {
            'title': 'Beautiful Photo',
            'creator': 'Jane Doe',
            'license': 'cc-by',
            'license_version': '4.0',
            'source': 'flickr',
        }
        result = generate_attribution(image)

        assert '"Beautiful Photo"' in result
        assert 'by Jane Doe' in result
        assert 'CC-BY 4.0' in result
        assert 'via flickr' in result

    def test_handles_missing_fields(self):
        image = {'license': 'cc0'}
        result = generate_attribution(image)
        assert 'CC0' in result

    def test_handles_empty_image(self):
        result = generate_attribution({})
        assert result == ''


class TestCompatibleLicenses:
    """Tests for license compatibility."""

    def test_includes_public_domain(self):
        assert 'pdm' in COMPATIBLE_LICENSES
        assert 'cc0' in COMPATIBLE_LICENSES

    def test_includes_compatible_cc_licenses(self):
        assert 'by' in COMPATIBLE_LICENSES
        assert 'by-sa' in COMPATIBLE_LICENSES

    def test_excludes_nc_licenses(self):
        # NC licenses are not Wikipedia-compatible
        assert 'by-nc' not in COMPATIBLE_LICENSES
        assert 'by-nc-sa' not in COMPATIBLE_LICENSES

    def test_excludes_nd_licenses(self):
        assert 'by-nd' not in COMPATIBLE_LICENSES
        assert 'by-nc-nd' not in COMPATIBLE_LICENSES


class TestIsCommonsImage:
    """Tests for Commons image detection."""

    def test_wikimedia_source(self):
        assert is_commons_image({'source': 'wikimedia'}) is True

    def test_wikimedia_source_case_insensitive(self):
        assert is_commons_image({'source': 'Wikimedia'}) is True

    def test_commons_foreign_url(self):
        img = {'source': 'flickr', 'foreign_landing_url': 'https://commons.wikimedia.org/wiki/File:Test.jpg'}
        assert is_commons_image(img) is True

    def test_commons_upload_url(self):
        img = {'source': 'other', 'url': 'https://upload.wikimedia.org/wikipedia/commons/3/3a/Test.jpg'}
        assert is_commons_image(img) is True

    def test_non_commons_image(self):
        img = {'source': 'flickr', 'url': 'https://flickr.com/photo.jpg', 'foreign_landing_url': 'https://flickr.com/123'}
        assert is_commons_image(img) is False

    def test_empty_image(self):
        assert is_commons_image({}) is False

    def test_none_values(self):
        assert is_commons_image({'source': None, 'url': None, 'foreign_landing_url': None}) is False


class TestGetCommonsFilename:
    """Tests for extracting Commons filenames from URLs."""

    def test_standard_commons_url(self):
        img = {'url': 'https://upload.wikimedia.org/wikipedia/commons/3/3a/Margaret_Thatcher_cropped2.png'}
        assert get_commons_filename(img) == 'Margaret_Thatcher_cropped2.png'

    def test_commons_url_with_encoded_chars(self):
        img = {'url': 'https://upload.wikimedia.org/wikipedia/commons/a/ab/Some_File%28test%29.jpg'}
        assert get_commons_filename(img) == 'Some_File%28test%29.jpg'

    def test_non_commons_url(self):
        img = {'url': 'https://flickr.com/photos/user/12345/photo.jpg'}
        assert get_commons_filename(img) == ''

    def test_empty_url(self):
        assert get_commons_filename({}) == ''
        assert get_commons_filename({'url': ''}) == ''

    def test_malformed_commons_url(self):
        img = {'url': 'https://upload.wikimedia.org/wikipedia/commons/'}
        assert get_commons_filename(img) == ''


class TestGenerateWikitext:
    """Tests for wikitext generation."""

    def test_basic_wikitext(self):
        result = generate_wikitext({}, 'Test.jpg', 'A test image')
        assert result == '[[File:Test.jpg|thumb|A test image]]'

    def test_wikitext_uses_image_title_as_fallback(self):
        img = {'title': 'Beautiful sunset'}
        result = generate_wikitext(img, 'Sunset.jpg')
        assert result == '[[File:Sunset.jpg|thumb|Beautiful sunset]]'

    def test_wikitext_empty_caption_and_no_title(self):
        result = generate_wikitext({}, 'Img.jpg')
        assert result == '[[File:Img.jpg|thumb|]]'


class TestSearchCommonsDetection:
    """Tests that search_images adds Commons detection fields."""

    @responses.activate
    def test_search_detects_commons_images(self):
        responses.add(
            responses.GET,
            'https://api.openverse.org/v1/images/',
            json={
                'result_count': 1,
                'page_count': 1,
                'results': [{
                    'id': 'abc',
                    'title': 'Test',
                    'thumbnail': 'https://example.com/thumb.jpg',
                    'url': 'https://upload.wikimedia.org/wikipedia/commons/3/3a/Test_file.jpg',
                    'foreign_landing_url': 'https://commons.wikimedia.org/wiki/File:Test_file.jpg',
                    'license': 'cc-by',
                    'license_url': 'https://creativecommons.org/licenses/by/4.0/',
                    'creator': 'Someone',
                    'source': 'wikimedia',
                }],
            },
            status=200,
        )

        result = search_images('test')
        img = result['results'][0]
        assert img['is_commons'] is True
        assert img['commons_filename'] == 'Test_file.jpg'

    @responses.activate
    def test_search_non_commons_has_no_filename(self):
        responses.add(
            responses.GET,
            'https://api.openverse.org/v1/images/',
            json={
                'result_count': 1,
                'page_count': 1,
                'results': [{
                    'id': 'def',
                    'title': 'Flickr photo',
                    'thumbnail': 'https://example.com/thumb.jpg',
                    'url': 'https://flickr.com/photo.jpg',
                    'foreign_landing_url': 'https://flickr.com/123',
                    'license': 'cc-by',
                    'license_url': 'https://creativecommons.org/licenses/by/4.0/',
                    'creator': 'Person',
                    'source': 'flickr',
                }],
            },
            status=200,
        )

        result = search_images('test')
        img = result['results'][0]
        assert img['is_commons'] is False
        assert 'commons_filename' not in img
