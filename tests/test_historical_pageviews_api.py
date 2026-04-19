"""Tests for pageviews_api URL construction and response parsing."""
from __future__ import annotations

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

    def test_partial_year_via_end_month(self):
        url = pageviews_api.build_url("Surgeon", 2016, 2026, end_month=3)
        assert url.endswith("monthly/2016010100/2026033100")

    def test_default_end_month_is_december(self):
        url = pageviews_api.build_url("Surgeon", 2016, 2025)
        assert url.endswith("monthly/2016010100/2025123100")


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


class TestExtractMonthlyViews:
    def test_extracts_year_month_views(self):
        items = [
            {"timestamp": "2016010100", "views": 100},
            {"timestamp": "2016020100", "views": 200},
            {"timestamp": "2026030100", "views": 50},
        ]
        result = pageviews_api.extract_monthly_views(items)
        assert result == [(2016, 1, 100), (2016, 2, 200), (2026, 3, 50)]

    def test_skips_missing_views(self):
        items = [{"timestamp": "2016010100"}]
        assert pageviews_api.extract_monthly_views(items) == []

    def test_skips_invalid_month(self):
        items = [{"timestamp": "2016130100", "views": 100}]  # month 13
        assert pageviews_api.extract_monthly_views(items) == []

    def test_skips_short_timestamp(self):
        items = [{"timestamp": "201601", "views": 100}]  # only 6 chars, valid
        result = pageviews_api.extract_monthly_views(items)
        assert result == [(2016, 1, 100)]
        items = [{"timestamp": "2016", "views": 100}]  # too short
        assert pageviews_api.extract_monthly_views(items) == []


class TestGroupByYear:
    def test_groups(self):
        monthly = [(2016, 1, 100), (2016, 2, 200), (2017, 1, 50)]
        grouped = pageviews_api.group_by_year(monthly)
        assert grouped == {2016: [(1, 100), (2, 200)], 2017: [(1, 50)]}

    def test_empty(self):
        assert pageviews_api.group_by_year([]) == {}
