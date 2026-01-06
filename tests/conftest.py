"""
Pytest fixtures for career-images tests.
"""

import os
import sys
import tempfile

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from db import SQLiteDatabase, VALID_STATUSES


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    db = SQLiteDatabase(db_path=path)
    db.init_schema()

    yield db

    # Cleanup
    os.unlink(path)


@pytest.fixture
def sample_careers():
    """Sample career data for testing."""
    return [
        {
            'wikidata_id': 'Q123',
            'name': 'Software Engineer',
            'category': 'profession',
            'wikipedia_url': 'https://en.wikipedia.org/wiki/Software_engineer',
        },
        {
            'wikidata_id': 'Q456',
            'name': 'Doctor',
            'category': 'profession',
            'wikipedia_url': 'https://en.wikipedia.org/wiki/Physician',
        },
        {
            'wikidata_id': 'Q789',
            'name': 'Teacher',
            'category': 'occupation',
            'wikipedia_url': 'https://en.wikipedia.org/wiki/Teacher',
        },
    ]


@pytest.fixture
def populated_db(temp_db, sample_careers):
    """Database populated with sample careers."""
    temp_db.upsert_careers(sample_careers)

    # Add some pageview data
    temp_db.update_pageviews('Q123', 365000, 1000.0)  # 1000/day
    temp_db.update_pageviews('Q456', 1825000, 5000.0)  # 5000/day
    temp_db.update_pageviews('Q789', 73000, 200.0)  # 200/day

    return temp_db


@pytest.fixture
def mock_openverse_response():
    """Sample Openverse API response."""
    return {
        'result_count': 2,
        'page_count': 1,
        'results': [
            {
                'id': 'abc123',
                'title': 'Female software engineer',
                'thumbnail': 'https://example.com/thumb1.jpg',
                'url': 'https://example.com/image1.jpg',
                'foreign_landing_url': 'https://flickr.com/photo/123',
                'license': 'cc-by',
                'license_url': 'https://creativecommons.org/licenses/by/4.0/',
                'creator': 'Jane Doe',
                'source': 'flickr',
            },
            {
                'id': 'def456',
                'title': 'Software developer at work',
                'thumbnail': 'https://example.com/thumb2.jpg',
                'url': 'https://example.com/image2.jpg',
                'foreign_landing_url': 'https://unsplash.com/photo/456',
                'license': 'cc0',
                'license_url': 'https://creativecommons.org/publicdomain/zero/1.0/',
                'creator': 'John Smith',
                'source': 'unsplash',
            },
        ],
    }
