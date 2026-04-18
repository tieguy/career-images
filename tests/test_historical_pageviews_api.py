"""Tests for pageviews_api URL construction and response parsing."""
from __future__ import annotations

import sys
from pathlib import Path

# Allow importing from analysis/historical-decline/
ANALYSIS_DIR = Path(__file__).parent.parent / "analysis" / "historical-decline"
sys.path.insert(0, str(ANALYSIS_DIR))

import pageviews_api


class TestBuildUrl:
    def test_simple_title(self):
        url = pageviews_api.build_url("Surgeon", 2016, 2025)
        assert url == (
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            "en.wikipedia/all-access/user/Surgeon/monthly/2016010100/2025123100"
        )

    def test_title_with_underscores_preserved(self):
        url = pageviews_api.build_url("Software_engineer", 2016, 2025)
        assert "/Software_engineer/" in url

    def test_title_with_slash_is_url_encoded(self):
        url = pageviews_api.build_url("AC/DC", 2016, 2025)
        assert "/AC%2FDC/" in url

    def test_custom_year_range(self):
        url = pageviews_api.build_url("Surgeon", 2020, 2022)
        assert url.endswith("monthly/2020010100/2022123100")


class TestExtractTitleFromUrl:
    def test_extracts_title_from_wikipedia_url(self):
        title = pageviews_api.extract_title_from_url(
            "https://en.wikipedia.org/wiki/Software_engineer"
        )
        assert title == "Software_engineer"

    def test_returns_empty_on_malformed_url(self):
        assert pageviews_api.extract_title_from_url("") == ""
        assert pageviews_api.extract_title_from_url("http://example.com") == ""


class TestSumMonthlyViews:
    def test_sums_items_by_year(self):
        items = [
            {"timestamp": "2016010100", "views": 100},
            {"timestamp": "2016020100", "views": 200},
            {"timestamp": "2017010100", "views": 50},
        ]
        totals = pageviews_api.sum_monthly_views_by_year(items)
        assert totals == {2016: 300, 2017: 50}

    def test_empty_items_returns_empty_dict(self):
        assert pageviews_api.sum_monthly_views_by_year([]) == {}

    def test_ignores_missing_views(self):
        items = [{"timestamp": "2016010100"}]  # no views key
        assert pageviews_api.sum_monthly_views_by_year(items) == {}

    def test_ignores_missing_timestamp(self):
        items = [{"views": 100}]  # no timestamp key
        assert pageviews_api.sum_monthly_views_by_year(items) == {}

    def test_ignores_non_numeric_timestamp(self):
        items = [{"timestamp": "bogus", "views": 100}]
        assert pageviews_api.sum_monthly_views_by_year(items) == {}
