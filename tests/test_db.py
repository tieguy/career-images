"""
Tests for db.py - Database operations.
"""

import pytest
from db import (
    get_pageview_bucket,
    get_category,
    VALID_STATUSES,
    PAGEVIEW_BUCKETS,
)


class TestGetPageviewBucket:
    """Tests for the pageview bucket calculation."""

    def test_high_traffic(self):
        idx, label = get_pageview_bucket(15000)
        assert label == '10,000+'
        assert idx == 0

    def test_boundary_10k(self):
        idx, label = get_pageview_bucket(10000)
        assert label == '10,000+'

    def test_just_below_10k(self):
        idx, label = get_pageview_bucket(9999)
        assert label == '5,000–10,000'

    def test_mid_range(self):
        idx, label = get_pageview_bucket(3000)
        assert label == '2,000–5,000'

    def test_low_traffic(self):
        idx, label = get_pageview_bucket(25)
        assert label == '<50'

    def test_zero_views(self):
        idx, label = get_pageview_bucket(0)
        assert label == '<50'

    def test_none_views(self):
        idx, label = get_pageview_bucket(None)
        assert label == '<50'


class TestGetCategory:
    """Tests for category mapping."""

    def test_profession(self):
        assert get_category('Q28640') == 'profession'

    def test_occupation(self):
        assert get_category('Q12737077') == 'occupation'

    def test_job(self):
        assert get_category('Q192581') == 'job'

    def test_position(self):
        assert get_category('Q4164871') == 'position'

    def test_unknown_defaults_to_profession(self):
        assert get_category('Q999999') == 'profession'


class TestDatabaseOperations:
    """Tests for database CRUD operations."""

    def test_init_schema(self, temp_db):
        # Schema already initialized by fixture
        stats = temp_db.get_stats()
        assert stats['total_careers'] == 0

    def test_upsert_career(self, temp_db, sample_careers):
        career = sample_careers[0]
        temp_db.upsert_career(career)

        result = temp_db.get_career(career['wikidata_id'])
        assert result is not None
        assert result['name'] == 'Software Engineer'
        assert result['category'] == 'profession'

    def test_upsert_careers_batch(self, temp_db, sample_careers):
        temp_db.upsert_careers(sample_careers)

        stats = temp_db.get_stats()
        assert stats['total_careers'] == 3

    def test_update_pageviews(self, temp_db, sample_careers):
        temp_db.upsert_career(sample_careers[0])
        temp_db.update_pageviews('Q123', 365000, 1000.0)

        career = temp_db.get_career('Q123')
        assert career['pageviews_total'] == 365000
        assert career['avg_daily_views'] == 1000.0

    def test_get_careers_needing_pageviews(self, temp_db, sample_careers):
        temp_db.upsert_careers(sample_careers)

        # All should need pageviews initially
        needing = temp_db.get_careers_needing_pageviews()
        assert len(needing) == 3

        # After updating one
        temp_db.update_pageviews('Q123', 100, 10.0)
        needing = temp_db.get_careers_needing_pageviews()
        assert len(needing) == 2


class TestBucketSorting:
    """Tests for bucket-based sorting."""

    def test_get_all_careers_sorted_by_bucket(self, populated_db):
        careers = populated_db.get_all_careers()

        # Doctor (5000/day) should be first (bucket 0: 5000-10000)
        # Software Engineer (1000/day) should be next (bucket 3: 1000-2000)
        # Teacher (200/day) should be last (bucket 5: 200-500)
        assert careers[0]['name'] == 'Doctor'
        assert careers[1]['name'] == 'Software Engineer'
        assert careers[2]['name'] == 'Teacher'

    def test_alphabetical_within_bucket(self, temp_db):
        # Add careers in same bucket
        careers = [
            {'wikidata_id': 'Q1', 'name': 'Zebra Keeper', 'category': 'job'},
            {'wikidata_id': 'Q2', 'name': 'Accountant', 'category': 'profession'},
            {'wikidata_id': 'Q3', 'name': 'Baker', 'category': 'job'},
        ]
        temp_db.upsert_careers(careers)

        # All in same bucket (no pageviews = <50)
        temp_db.update_pageviews('Q1', 10, 30.0)
        temp_db.update_pageviews('Q2', 10, 30.0)
        temp_db.update_pageviews('Q3', 10, 30.0)

        result = temp_db.get_all_careers()
        names = [c['name'] for c in result]
        assert names == ['Accountant', 'Baker', 'Zebra Keeper']


class TestStatusOperations:
    """Tests for status updates."""

    def test_valid_statuses(self):
        assert 'unreviewed' in VALID_STATUSES
        assert 'needs_diverse_images' in VALID_STATUSES
        assert 'has_diverse_images' in VALID_STATUSES
        assert 'not_a_career' in VALID_STATUSES
        assert 'gender_specific' in VALID_STATUSES

    def test_update_status(self, populated_db):
        populated_db.update_career_status(
            'Q123',
            'needs_diverse_images',
            reviewed_by='tester',
            notes='Needs more diverse images'
        )

        career = populated_db.get_career('Q123')
        assert career['status'] == 'needs_diverse_images'
        assert career['reviewed_by'] == 'tester'
        assert career['notes'] == 'Needs more diverse images'

    def test_get_careers_by_status(self, populated_db):
        populated_db.update_career_status('Q123', 'needs_diverse_images')
        populated_db.update_career_status('Q456', 'needs_diverse_images')

        results = populated_db.get_careers_by_status('needs_diverse_images')
        assert len(results) == 2


class TestImageOperations:
    """Tests for career image operations."""

    def test_set_replacement_image(self, populated_db):
        populated_db.set_replacement_image(
            'Q123',
            'https://example.com/image.jpg',
            'A software engineer',
            creator='Jane Doe',
            license='cc-by',
            license_url='https://creativecommons.org/licenses/by/4.0/',
            source_url='https://flickr.com/photo/123'
        )

        images = populated_db.get_career_images('Q123')
        assert len(images) == 1
        assert images[0]['is_replacement'] == 1
        assert images[0]['image_url'] == 'https://example.com/image.jpg'

    def test_replacement_replaces_previous(self, populated_db):
        # Set first replacement
        populated_db.set_replacement_image('Q123', 'https://example.com/old.jpg', 'Old')

        # Set second replacement
        populated_db.set_replacement_image('Q123', 'https://example.com/new.jpg', 'New')

        images = populated_db.get_career_images('Q123')
        replacements = [i for i in images if i['is_replacement']]
        assert len(replacements) == 1
        assert replacements[0]['image_url'] == 'https://example.com/new.jpg'


class TestSearch:
    """Tests for search functionality."""

    def test_search_by_name(self, populated_db):
        results = populated_db.search_careers('Engineer')
        assert len(results) == 1
        assert results[0]['name'] == 'Software Engineer'

    def test_search_case_insensitive(self, populated_db):
        results = populated_db.search_careers('doctor')
        assert len(results) == 1
        assert results[0]['name'] == 'Doctor'

    def test_search_no_results(self, populated_db):
        results = populated_db.search_careers('Astronaut')
        assert len(results) == 0
