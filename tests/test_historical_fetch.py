"""Tests for fetch_history.py — the async fetcher and its CLI glue."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import responses
from responses import matchers

ANALYSIS_DIR = Path(__file__).parent.parent / "analysis" / "historical-decline"
sys.path.insert(0, str(ANALYSIS_DIR))

import fetch_history
import history_db
import pageviews_api


@pytest.fixture
def fresh_db(tmp_path):
    db_path = tmp_path / "history.db"
    history_db.init_schema(db_path)
    return db_path


def _mock_response_items(years: range, monthly_views: int = 100) -> list[dict]:
    """Build a fake items[] list with `monthly_views` per month across the year range."""
    items = []
    for year in years:
        for month in range(1, 13):
            items.append({
                "timestamp": f"{year}{month:02d}0100",
                "views": monthly_views,
            })
    return items


class TestFetchHistoryEndToEnd:
    """End-to-end tests using monkeypatch on the private _http_get_json seam.

    Test strategy rationale: the project's `responses` library does not
    intercept aiohttp by default, and we deliberately avoid adding
    `aioresponses` as a new dev dependency for a single test. Instead,
    `_http_get_json` is factored as a private seam in fetch_history.py that
    tests replace via monkeypatch. URL construction and response parsing are
    covered separately by tests against `pageviews_api`. User-Agent header
    correctness (critical for avoiding 403s from Wikimedia) is verified as a
    manual smoke-test step at the end of this phase — see Step 5.
    """

    def test_fetch_single_career_writes_annual_totals(
        self, fresh_db, monkeypatch
    ):
        career = {"wikidata_id": "Q123", "wikipedia_url": "https://en.wikipedia.org/wiki/Surgeon"}
        items = _mock_response_items(range(2016, 2026), monthly_views=100)

        async def fake_fetch(session, url):
            # url is the pageviews URL; return a stubbed response body.
            assert "/Surgeon/" in url
            return 200, {"items": items}

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)

        asyncio.run(fetch_history.fetch_all([career], db_path=fresh_db, concurrency=5))

        with history_db.get_connection(fresh_db) as conn:
            rows = conn.execute(
                "SELECT year, views FROM annual_totals WHERE wikidata_id='Q123' ORDER BY year"
            ).fetchall()
            log = conn.execute(
                "SELECT status FROM fetch_log WHERE wikidata_id='Q123'"
            ).fetchone()

        # 10 complete years × 12 months × 100 views = 1200 per year
        assert len(rows) == 10
        assert rows[0]["year"] == 2016
        assert rows[0]["views"] == 1200
        assert rows[-1]["year"] == 2025
        assert log["status"] == "ok"

    def test_404_records_missing(self, fresh_db, monkeypatch):
        career = {"wikidata_id": "Q404", "wikipedia_url": "https://en.wikipedia.org/wiki/NonExistent"}

        async def fake_fetch(session, url):
            return 404, None

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)
        asyncio.run(fetch_history.fetch_all([career], db_path=fresh_db, concurrency=5))

        with history_db.get_connection(fresh_db) as conn:
            row = conn.execute(
                "SELECT status, error FROM fetch_log WHERE wikidata_id='Q404'"
            ).fetchone()
            totals = conn.execute(
                "SELECT COUNT(*) AS n FROM annual_totals WHERE wikidata_id='Q404'"
            ).fetchone()
        assert row["status"] == "missing"
        assert totals["n"] == 0

    def test_empty_items_records_missing(self, fresh_db, monkeypatch):
        career = {"wikidata_id": "Q0", "wikipedia_url": "https://en.wikipedia.org/wiki/Empty"}

        async def fake_fetch(session, url):
            return 200, {"items": []}

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)
        asyncio.run(fetch_history.fetch_all([career], db_path=fresh_db, concurrency=5))

        with history_db.get_connection(fresh_db) as conn:
            row = conn.execute(
                "SELECT status FROM fetch_log WHERE wikidata_id='Q0'"
            ).fetchone()
        assert row["status"] == "missing"

    def test_exception_records_error(self, fresh_db, monkeypatch):
        career = {"wikidata_id": "QX", "wikipedia_url": "https://en.wikipedia.org/wiki/X"}

        async def fake_fetch(session, url):
            raise TimeoutError("boom")

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)
        asyncio.run(fetch_history.fetch_all([career], db_path=fresh_db, concurrency=5))

        with history_db.get_connection(fresh_db) as conn:
            row = conn.execute(
                "SELECT status, error FROM fetch_log WHERE wikidata_id='QX'"
            ).fetchone()
        assert row["status"] == "error"
        assert "boom" in (row["error"] or "")


class TestResume:
    def test_resume_skips_ok_rows(self, fresh_db, monkeypatch):
        # Pre-populate fetch_log: Q1 is already ok; Q2 is errored; Q3 has no row.
        history_db.record_fetch_status("Q1", "A", "ok", None, db_path=fresh_db)
        history_db.record_fetch_status("Q2", "B", "error", "x", db_path=fresh_db)

        careers = [
            {"wikidata_id": "Q1", "wikipedia_url": "https://en.wikipedia.org/wiki/A"},
            {"wikidata_id": "Q2", "wikipedia_url": "https://en.wikipedia.org/wiki/B"},
            {"wikidata_id": "Q3", "wikipedia_url": "https://en.wikipedia.org/wiki/C"},
        ]
        fetched_urls: list[str] = []

        async def fake_fetch(session, url):
            fetched_urls.append(url)
            return 200, {"items": []}

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)
        asyncio.run(fetch_history.resume(careers, db_path=fresh_db, concurrency=5))

        # Q1 should be skipped; Q2 and Q3 should be fetched.
        fetched_titles = {url.split("/user/")[-1].split("/monthly")[0] for url in fetched_urls}
        assert fetched_titles == {"B", "C"}
