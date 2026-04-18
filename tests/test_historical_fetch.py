"""Tests for fetch_history.py — the async fetcher and its CLI glue."""
from __future__ import annotations

import asyncio

import aiohttp
import pytest
import responses
from responses import matchers

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

    def test_empty_title_records_error(self, fresh_db, monkeypatch):
        career = {"wikidata_id": "QEMPTY", "wikipedia_url": ""}

        # Track if _http_get_json was called; it should NOT be (short-circuit before HTTP).
        http_called = []

        async def fake_fetch(session, url):
            http_called.append(True)
            return 200, {"items": []}

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)
        asyncio.run(fetch_history.fetch_all([career], db_path=fresh_db, concurrency=5))

        with history_db.get_connection(fresh_db) as conn:
            row = conn.execute(
                "SELECT status, error FROM fetch_log WHERE wikidata_id='QEMPTY'"
            ).fetchone()
        assert row["status"] == "error"
        assert row["error"] == "no title in url"
        assert not http_called, "HTTP should not be called for empty title"

    def test_all_zero_views_records_missing(self, fresh_db, monkeypatch):
        """All-zero pageviews across the entire range are treated as title drift."""
        career = {"wikidata_id": "QZERO", "wikipedia_url": "https://en.wikipedia.org/wiki/Drifted"}
        # Create items with all zeros across 10 years × 12 months = 120 items.
        items = _mock_response_items(range(2016, 2026), monthly_views=0)

        async def fake_fetch(session, url):
            return 200, {"items": items}

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)
        asyncio.run(fetch_history.fetch_all([career], db_path=fresh_db, concurrency=5))

        with history_db.get_connection(fresh_db) as conn:
            log_row = conn.execute(
                "SELECT status, error FROM fetch_log WHERE wikidata_id='QZERO'"
            ).fetchone()
            totals_count = conn.execute(
                "SELECT COUNT(*) AS n FROM annual_totals WHERE wikidata_id='QZERO'"
            ).fetchone()

        assert log_row["status"] == "missing"
        assert log_row["error"] == "all-zero views"
        assert totals_count["n"] == 0, "No annual totals should be written for all-zero data"


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


class TestRetry:
    """Test retry logic with Retry-After header and exponential backoff."""

    async def _async_noop(self, *args, **kwargs):
        """Async no-op for mocking asyncio.sleep."""
        pass

    def test_retries_on_429_then_succeeds(self, fresh_db, monkeypatch):
        """On 429, parse Retry-After header and sleep, then succeed on retry."""
        career = {"wikidata_id": "Q429", "wikipedia_url": "https://en.wikipedia.org/wiki/Throttled"}
        items = _mock_response_items(range(2016, 2026), monthly_views=100)

        # Track call count to _http_get_raw.
        call_count = []

        async def fake_http_get_raw(session, url):
            call_count.append(None)
            if len(call_count) == 1:
                # First call: return 429 with Retry-After header.
                return 429, None, {"retry-after": "0.01"}
            else:
                # Second call: success.
                return 200, {"items": items}, {}

        # Monkeypatch asyncio.sleep to be fast.
        monkeypatch.setattr(asyncio, "sleep", self._async_noop)
        monkeypatch.setattr(fetch_history, "_http_get_raw", fake_http_get_raw)

        asyncio.run(fetch_history.fetch_all([career], db_path=fresh_db, concurrency=5))

        # Verify the fetcher succeeded and wrote data.
        with history_db.get_connection(fresh_db) as conn:
            log = conn.execute(
                "SELECT status FROM fetch_log WHERE wikidata_id='Q429'"
            ).fetchone()
            totals_count = conn.execute(
                "SELECT COUNT(*) AS n FROM annual_totals WHERE wikidata_id='Q429'"
            ).fetchone()

        assert log["status"] == "ok"
        assert totals_count["n"] == 10, "Should write annual totals for all 10 years"
        assert len(call_count) == 2, "_http_get_raw should be called twice"

    def test_retries_on_500_then_succeeds(self, fresh_db, monkeypatch):
        """On 503, use exponential backoff (no Retry-After), then succeed."""
        career = {"wikidata_id": "Q503", "wikipedia_url": "https://en.wikipedia.org/wiki/ServerError"}
        items = _mock_response_items(range(2016, 2026), monthly_views=50)

        call_count = []

        async def fake_http_get_raw(session, url):
            call_count.append(None)
            if len(call_count) == 1:
                # First call: return 503 without Retry-After.
                return 503, None, {}
            else:
                # Second call: success.
                return 200, {"items": items}, {}

        monkeypatch.setattr(asyncio, "sleep", self._async_noop)
        monkeypatch.setattr(fetch_history, "_http_get_raw", fake_http_get_raw)

        asyncio.run(fetch_history.fetch_all([career], db_path=fresh_db, concurrency=5))

        with history_db.get_connection(fresh_db) as conn:
            log = conn.execute(
                "SELECT status FROM fetch_log WHERE wikidata_id='Q503'"
            ).fetchone()

        assert log["status"] == "ok"
        assert len(call_count) == 2, "_http_get_raw should be called twice"

    def test_exhausts_retries_after_persistent_429(self, fresh_db, monkeypatch):
        """After MAX_RETRIES, return final 429. Caller treats it as missing."""
        career = {"wikidata_id": "QPERSIST", "wikipedia_url": "https://en.wikipedia.org/wiki/AlwaysBusy"}

        call_count = []

        async def fake_http_get_raw(session, url):
            call_count.append(None)
            # Always return 429.
            return 429, None, {}

        monkeypatch.setattr(asyncio, "sleep", self._async_noop)
        monkeypatch.setattr(fetch_history, "_http_get_raw", fake_http_get_raw)

        asyncio.run(fetch_history.fetch_all([career], db_path=fresh_db, concurrency=5))

        with history_db.get_connection(fresh_db) as conn:
            log = conn.execute(
                "SELECT status, error FROM fetch_log WHERE wikidata_id='QPERSIST'"
            ).fetchone()
            totals_count = conn.execute(
                "SELECT COUNT(*) AS n FROM annual_totals WHERE wikidata_id='QPERSIST'"
            ).fetchone()

        # After MAX_RETRIES exhausted, the final 429 is returned to _fetch_one,
        # which treats it as missing (status != 200 and body is None).
        assert log["status"] == "missing"
        assert totals_count["n"] == 0
        # _http_get_raw should be called exactly MAX_RETRIES + 1 = 4 times.
        assert len(call_count) == fetch_history.MAX_RETRIES + 1

    def test_retries_on_client_error_then_succeeds(self, fresh_db, monkeypatch):
        """On aiohttp.ClientError, retry with exponential backoff, then succeed."""
        career = {"wikidata_id": "QCLIENT", "wikipedia_url": "https://en.wikipedia.org/wiki/NetworkDown"}
        items = _mock_response_items(range(2016, 2026), monthly_views=75)

        call_count = []

        async def fake_http_get_raw(session, url):
            call_count.append(None)
            if len(call_count) == 1:
                # First call: raise ClientError. This gets caught by _http_get_json's
                # exception handler, which retries after sleeping.
                raise aiohttp.ClientError("network down")
            else:
                # Second call: success.
                return 200, {"items": items}, {}

        monkeypatch.setattr(asyncio, "sleep", self._async_noop)
        monkeypatch.setattr(fetch_history, "_http_get_raw", fake_http_get_raw)

        asyncio.run(fetch_history.fetch_all([career], db_path=fresh_db, concurrency=5))

        with history_db.get_connection(fresh_db) as conn:
            log = conn.execute(
                "SELECT status, error FROM fetch_log WHERE wikidata_id='QCLIENT'"
            ).fetchone()
            totals_count = conn.execute(
                "SELECT COUNT(*) AS n FROM annual_totals WHERE wikidata_id='QCLIENT'"
            ).fetchone()

        assert log["status"] == "ok"
        assert totals_count["n"] == 10
        assert len(call_count) == 2
