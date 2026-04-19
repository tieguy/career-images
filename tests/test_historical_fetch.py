"""Tests for fetch_history.py — the synchronous fetcher and its CLI glue."""
from __future__ import annotations

import pytest
import requests

import fetch_history
import history_db


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

    Tests stub the low-level HTTP seam so they exercise the fetcher's branching
    without making real network calls. URL construction and response parsing
    are covered by pageviews_api tests; User-Agent correctness is verified by
    the manual smoke test documented in the Phase 2 plan.
    """

    def test_fetch_single_career_writes_annual_totals(self, fresh_db, monkeypatch):
        career = {"wikidata_id": "Q123", "wikipedia_url": "https://en.wikipedia.org/wiki/Surgeon"}
        items = _mock_response_items(range(2016, 2026), monthly_views=100)

        def fake_fetch(session, url):
            assert "/Surgeon/" in url
            return 200, {"items": items}

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)

        fetch_history.fetch_all([career], db_path=fresh_db, delay=0)

        with history_db.get_connection(fresh_db) as conn:
            rows = conn.execute(
                "SELECT year, views FROM annual_totals WHERE wikidata_id='Q123' ORDER BY year"
            ).fetchall()
            monthly = conn.execute(
                "SELECT COUNT(*) AS n FROM monthly_views WHERE wikidata_id='Q123'"
            ).fetchone()
            log = conn.execute(
                "SELECT status FROM fetch_log WHERE wikidata_id='Q123'"
            ).fetchone()

        assert len(rows) == 10
        assert rows[0]["year"] == 2016
        assert rows[0]["views"] == 1200
        assert rows[-1]["year"] == 2025
        assert monthly["n"] == 120, "Expected 120 monthly rows (10 years × 12 months)"
        assert log["status"] == "ok"

    def test_partial_year_writes_monthly_but_not_annual(self, fresh_db, monkeypatch):
        """Incomplete years (e.g., 3 months of 2026) go to monthly_views only."""
        career = {"wikidata_id": "QPART", "wikipedia_url": "https://en.wikipedia.org/wiki/Partial"}
        # 10 complete years of 2016-2025 plus 3 months of 2026 (Jan, Feb, Mar).
        full_years = _mock_response_items(range(2016, 2026), monthly_views=100)
        partial_2026 = [
            {"timestamp": "2026010100", "views": 200},
            {"timestamp": "2026020100", "views": 201},
            {"timestamp": "2026030100", "views": 202},
        ]
        items = full_years + partial_2026

        def fake_fetch(session, url):
            return 200, {"items": items}

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)
        fetch_history.fetch_all([career], db_path=fresh_db, delay=0)

        with history_db.get_connection(fresh_db) as conn:
            annual_years = [
                r["year"] for r in conn.execute(
                    "SELECT year FROM annual_totals WHERE wikidata_id='QPART' ORDER BY year"
                ).fetchall()
            ]
            monthly_2026 = conn.execute(
                "SELECT COUNT(*) AS n FROM monthly_views "
                "WHERE wikidata_id='QPART' AND year=2026"
            ).fetchone()

        assert annual_years == list(range(2016, 2026)), "No annual row for partial 2026"
        assert monthly_2026["n"] == 3, "Monthly rows for Q1 2026 should be present"

    def test_404_records_missing(self, fresh_db, monkeypatch):
        career = {"wikidata_id": "Q404", "wikipedia_url": "https://en.wikipedia.org/wiki/NonExistent"}

        def fake_fetch(session, url):
            return 404, None

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)
        fetch_history.fetch_all([career], db_path=fresh_db, delay=0)

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

        def fake_fetch(session, url):
            return 200, {"items": []}

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)
        fetch_history.fetch_all([career], db_path=fresh_db, delay=0)

        with history_db.get_connection(fresh_db) as conn:
            row = conn.execute(
                "SELECT status FROM fetch_log WHERE wikidata_id='Q0'"
            ).fetchone()
        assert row["status"] == "missing"

    def test_exception_records_error(self, fresh_db, monkeypatch):
        career = {"wikidata_id": "QX", "wikipedia_url": "https://en.wikipedia.org/wiki/X"}

        def fake_fetch(session, url):
            raise TimeoutError("boom")

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)
        fetch_history.fetch_all([career], db_path=fresh_db, delay=0)

        with history_db.get_connection(fresh_db) as conn:
            row = conn.execute(
                "SELECT status, error FROM fetch_log WHERE wikidata_id='QX'"
            ).fetchone()
        assert row["status"] == "error"
        assert "boom" in (row["error"] or "")

    def test_empty_title_records_error(self, fresh_db, monkeypatch):
        career = {"wikidata_id": "QEMPTY", "wikipedia_url": ""}
        http_called = []

        def fake_fetch(session, url):
            http_called.append(True)
            return 200, {"items": []}

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)
        fetch_history.fetch_all([career], db_path=fresh_db, delay=0)

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
        items = _mock_response_items(range(2016, 2026), monthly_views=0)

        def fake_fetch(session, url):
            return 200, {"items": items}

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)
        fetch_history.fetch_all([career], db_path=fresh_db, delay=0)

        with history_db.get_connection(fresh_db) as conn:
            log_row = conn.execute(
                "SELECT status, error FROM fetch_log WHERE wikidata_id='QZERO'"
            ).fetchone()
            totals_count = conn.execute(
                "SELECT COUNT(*) AS n FROM annual_totals WHERE wikidata_id='QZERO'"
            ).fetchone()

        assert log_row["status"] == "missing"
        assert log_row["error"] == "all-zero views"
        assert totals_count["n"] == 0


class TestResume:
    def test_resume_skips_ok_rows(self, fresh_db, monkeypatch):
        history_db.record_fetch_status("Q1", "A", "ok", None, db_path=fresh_db)
        history_db.record_fetch_status("Q2", "B", "error", "x", db_path=fresh_db)

        careers = [
            {"wikidata_id": "Q1", "wikipedia_url": "https://en.wikipedia.org/wiki/A"},
            {"wikidata_id": "Q2", "wikipedia_url": "https://en.wikipedia.org/wiki/B"},
            {"wikidata_id": "Q3", "wikipedia_url": "https://en.wikipedia.org/wiki/C"},
        ]
        fetched_urls: list[str] = []

        def fake_fetch(session, url):
            fetched_urls.append(url)
            return 200, {"items": []}

        monkeypatch.setattr(fetch_history, "_http_get_json", fake_fetch)
        fetch_history.resume(careers, db_path=fresh_db, delay=0)

        fetched_titles = {url.split("/user/")[-1].split("/monthly")[0] for url in fetched_urls}
        assert fetched_titles == {"B", "C"}


class TestRetry:
    """Test retry logic with Retry-After header and exponential backoff."""

    def _noop_sleep(self, *args, **kwargs):
        """Sync no-op for mocking time.sleep."""
        pass

    def test_retries_on_429_then_succeeds(self, fresh_db, monkeypatch):
        """On 429, parse Retry-After header and sleep, then succeed on retry."""
        career = {"wikidata_id": "Q429", "wikipedia_url": "https://en.wikipedia.org/wiki/Throttled"}
        items = _mock_response_items(range(2016, 2026), monthly_views=100)

        call_count = []

        def fake_http_get_raw(session, url):
            call_count.append(None)
            if len(call_count) == 1:
                return 429, None, {"retry-after": "0.01"}
            return 200, {"items": items}, {}

        monkeypatch.setattr("time.sleep", self._noop_sleep)
        monkeypatch.setattr(fetch_history, "_http_get_raw", fake_http_get_raw)

        fetch_history.fetch_all([career], db_path=fresh_db, delay=0)

        with history_db.get_connection(fresh_db) as conn:
            log = conn.execute(
                "SELECT status FROM fetch_log WHERE wikidata_id='Q429'"
            ).fetchone()
            totals_count = conn.execute(
                "SELECT COUNT(*) AS n FROM annual_totals WHERE wikidata_id='Q429'"
            ).fetchone()

        assert log["status"] == "ok"
        assert totals_count["n"] == 10
        assert len(call_count) == 2

    def test_retries_on_500_then_succeeds(self, fresh_db, monkeypatch):
        """On 503, use exponential backoff (no Retry-After), then succeed."""
        career = {"wikidata_id": "Q503", "wikipedia_url": "https://en.wikipedia.org/wiki/ServerError"}
        items = _mock_response_items(range(2016, 2026), monthly_views=50)

        call_count = []

        def fake_http_get_raw(session, url):
            call_count.append(None)
            if len(call_count) == 1:
                return 503, None, {}
            return 200, {"items": items}, {}

        monkeypatch.setattr("time.sleep", self._noop_sleep)
        monkeypatch.setattr(fetch_history, "_http_get_raw", fake_http_get_raw)

        fetch_history.fetch_all([career], db_path=fresh_db, delay=0)

        with history_db.get_connection(fresh_db) as conn:
            log = conn.execute(
                "SELECT status FROM fetch_log WHERE wikidata_id='Q503'"
            ).fetchone()

        assert log["status"] == "ok"
        assert len(call_count) == 2

    def test_exhausts_retries_after_persistent_429(self, fresh_db, monkeypatch):
        """After MAX_RETRIES, return final 429. Caller treats it as missing."""
        career = {"wikidata_id": "QPERSIST", "wikipedia_url": "https://en.wikipedia.org/wiki/AlwaysBusy"}

        call_count = []

        def fake_http_get_raw(session, url):
            call_count.append(None)
            return 429, None, {}

        monkeypatch.setattr("time.sleep", self._noop_sleep)
        monkeypatch.setattr(fetch_history, "_http_get_raw", fake_http_get_raw)

        fetch_history.fetch_all([career], db_path=fresh_db, delay=0)

        with history_db.get_connection(fresh_db) as conn:
            log = conn.execute(
                "SELECT status, error FROM fetch_log WHERE wikidata_id='QPERSIST'"
            ).fetchone()
            totals_count = conn.execute(
                "SELECT COUNT(*) AS n FROM annual_totals WHERE wikidata_id='QPERSIST'"
            ).fetchone()

        assert log["status"] == "missing"
        assert totals_count["n"] == 0
        assert len(call_count) == fetch_history.MAX_RETRIES + 1

    def test_retries_on_request_exception_then_succeeds(self, fresh_db, monkeypatch):
        """On requests.RequestException (network error), retry with backoff, then succeed."""
        career = {"wikidata_id": "QCLIENT", "wikipedia_url": "https://en.wikipedia.org/wiki/NetworkDown"}
        items = _mock_response_items(range(2016, 2026), monthly_views=75)

        call_count = []

        def fake_http_get_raw(session, url):
            call_count.append(None)
            if len(call_count) == 1:
                raise requests.ConnectionError("network down")
            return 200, {"items": items}, {}

        monkeypatch.setattr("time.sleep", self._noop_sleep)
        monkeypatch.setattr(fetch_history, "_http_get_raw", fake_http_get_raw)

        fetch_history.fetch_all([career], db_path=fresh_db, delay=0)

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
