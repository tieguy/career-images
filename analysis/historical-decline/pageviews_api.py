"""Wikimedia Pageviews REST API client helpers.

Stateless functions. Network calls live in fetch_history.py; this module only
handles URL construction and response parsing so it can be unit-tested without
mocks.
"""
from __future__ import annotations

from collections import defaultdict
from urllib.parse import quote

BASE_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia/all-access/user"
)
USER_AGENT = (
    "WikipediaCareerImages-Historical/1.0 "
    "(User:LuisVilla; https://en.wikipedia.org/wiki/User:LuisVilla/ImageDiversityTool)"
)


def build_url(title: str, start_year: int, end_year: int) -> str:
    """Construct the Pageviews API URL for a single article over a year range.

    Title is URL-encoded (quote with safe=''), preserving underscores naturally
    since they are unreserved characters. Slashes and other special chars are
    percent-encoded, matching the behavior of python-mwviews.
    """
    encoded = quote(title, safe="")
    start = f"{start_year}010100"
    end = f"{end_year}123100"
    return f"{BASE_URL}/{encoded}/monthly/{start}/{end}"


def extract_title_from_url(wikipedia_url: str) -> str:
    """Extract the article title from a Wikipedia URL.

    Mirrors fetcher.py:extract_title_from_url exactly so both modules agree.
    """
    if "/wiki/" not in wikipedia_url:
        return ""
    return wikipedia_url.split("/wiki/")[-1]


def sum_monthly_views_by_year(items: list[dict]) -> dict[int, int]:
    """Sum a response's items[] into {year: total_views}.

    The API returns one item per month for monthly granularity, with timestamps
    of the form YYYYMM0100. Items missing a views field are skipped (defensive
    against API oddities).
    """
    totals: dict[int, int] = defaultdict(int)
    for item in items:
        if "views" not in item:
            continue
        ts = item.get("timestamp", "")
        if len(ts) < 4:
            continue
        try:
            year = int(ts[:4])
        except ValueError:
            continue
        totals[year] += int(item["views"])
    return dict(totals)
