# Historical Pageview Analysis — Phase 2: History Fetcher

> **For Claude:** REQUIRED SUB-SKILL: Use ed3d-plan-and-execute:executing-an-implementation-plan to implement this plan task-by-task.

**Goal:** Implement `fetch_history.py` — an async CLI that pulls 120 months of pageview data (2016-01 to 2025-12) for every career in `careers.db` from the Wikimedia Pageviews REST API, sums into annual totals, and writes to `history.db` with resume-on-failure support.

**Architecture:** Async fetcher using `aiohttp.ClientSession` + `asyncio.Semaphore`, mirroring the exact structure of `fetcher.py:fetch_pageviews_batch` (lines 243–268). Reads career list from `careers.db` via `db.get_database().get_all_careers()`. Writes to `history.db` via the Phase 1 `history_db` module. Fetch state tracked in `fetch_log` so `resume` can pick up incomplete or errored rows.

**Tech Stack:** Python 3.13, aiohttp==3.11.11 (already a runtime dep), sqlite3, responses==0.25.3 (dev dep for HTTP mocking in tests).

**Scope:** Phase 2 of 4.

**Codebase verified:** 2026-04-18. Wikimedia Pageviews API contract confirmed via docs:
- Endpoint: `GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{title}/monthly/{start}/{end}` where start/end are `YYYYMMDDHH` strings.
- Response shape: `{"items": [{"timestamp": "YYYYMM0100", "views": int, ...}, ...]}`.
- Current month excluded from response (24–48h processing latency).
- `agent=user` filters bots and spiders.
- User-Agent header required; non-compliant UAs get 403.
- Rate limits: ~500 req/hr unauthenticated, higher with compliant UA (recommended format: `Tool/version (contact)`).

**User-Agent decision:** Use `WikipediaCareerImages-Historical/1.0 (User:LuisVilla; https://en.wikipedia.org/wiki/User:LuisVilla/ImageDiversityTool)` — compliant format, explicit enough to avoid 403, identifies the tool for Wikimedia's ops team.

---

## Task 1: Wikipedia title encoding helper and URL builder

**Files:**
- Create: `analysis/historical-decline/pageviews_api.py`
- Create: `tests/test_historical_pageviews_api.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_historical_pageviews_api.py -v
```
Expected: `ModuleNotFoundError: No module named 'pageviews_api'`.

**Step 3: Implement `pageviews_api.py`**

```python
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
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_historical_pageviews_api.py -v
```
Expected: all 8 tests pass.

**Step 5: Commit**

```bash
git add analysis/historical-decline/pageviews_api.py tests/test_historical_pageviews_api.py
git commit -m "feat(analysis): add pageviews API URL and response helpers"
```

---

## Task 2: history_db write helpers

**Files:**
- Modify: `analysis/historical-decline/history_db.py` (append new functions; do not change existing `connect`/`get_connection`/`init_schema`/`table_names`)
- Create: `tests/test_historical_db.py`

**Step 1: Write the failing test**

```python
"""Tests for history_db write helpers."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ANALYSIS_DIR = Path(__file__).parent.parent / "analysis" / "historical-decline"
sys.path.insert(0, str(ANALYSIS_DIR))

import history_db


@pytest.fixture
def fresh_db(tmp_path):
    db_path = tmp_path / "history.db"
    history_db.init_schema(db_path)
    return db_path


class TestUpsertAnnualTotals:
    def test_inserts_rows(self, fresh_db):
        rows = [
            ("Q123", "Surgeon", 2016, 12345),
            ("Q123", "Surgeon", 2017, 10000),
        ]
        history_db.upsert_annual_totals(rows, db_path=fresh_db)
        with history_db.get_connection(fresh_db) as conn:
            result = conn.execute(
                "SELECT wikidata_id, year, views FROM annual_totals ORDER BY year"
            ).fetchall()
        assert len(result) == 2
        assert result[0]["views"] == 12345
        assert result[1]["views"] == 10000

    def test_replaces_on_conflict(self, fresh_db):
        history_db.upsert_annual_totals(
            [("Q123", "Surgeon", 2016, 100)], db_path=fresh_db
        )
        history_db.upsert_annual_totals(
            [("Q123", "Surgeon", 2016, 999)], db_path=fresh_db
        )
        with history_db.get_connection(fresh_db) as conn:
            (views,) = conn.execute(
                "SELECT views FROM annual_totals WHERE wikidata_id='Q123' AND year=2016"
            ).fetchone()
        assert views == 999


class TestFetchLog:
    def test_records_ok_status(self, fresh_db):
        history_db.record_fetch_status(
            "Q123", "Surgeon", "ok", error=None, db_path=fresh_db
        )
        with history_db.get_connection(fresh_db) as conn:
            row = conn.execute(
                "SELECT wikidata_id, status, error FROM fetch_log"
            ).fetchone()
        assert row["wikidata_id"] == "Q123"
        assert row["status"] == "ok"
        assert row["error"] is None

    def test_records_error_with_message(self, fresh_db):
        history_db.record_fetch_status(
            "Q456", "Nonexistent", "error", error="timeout", db_path=fresh_db
        )
        with history_db.get_connection(fresh_db) as conn:
            row = conn.execute(
                "SELECT status, error FROM fetch_log WHERE wikidata_id='Q456'"
            ).fetchone()
        assert row["status"] == "error"
        assert row["error"] == "timeout"

    def test_rejects_invalid_status(self, fresh_db):
        # CHECK constraint in schema should reject this
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            history_db.record_fetch_status(
                "Q789", "Foo", "bogus", error=None, db_path=fresh_db
            )

    def test_get_qids_needing_fetch_excludes_ok(self, fresh_db):
        history_db.record_fetch_status("Q1", "A", "ok", None, db_path=fresh_db)
        history_db.record_fetch_status("Q2", "B", "error", "x", db_path=fresh_db)
        history_db.record_fetch_status("Q3", "C", "missing", None, db_path=fresh_db)
        all_qids = {"Q1", "Q2", "Q3", "Q4"}
        needing = history_db.get_qids_needing_fetch(all_qids, db_path=fresh_db)
        assert needing == {"Q2", "Q3", "Q4"}
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/test_historical_db.py -v
```
Expected: `AttributeError` on `upsert_annual_totals` / `record_fetch_status` / `get_qids_needing_fetch`.

**Step 3: Append the helpers to `history_db.py`**

Add the following functions at the end of `analysis/historical-decline/history_db.py`:

```python
from datetime import datetime, timezone


def upsert_annual_totals(
    rows: list[tuple[str, str, int, int]],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """Insert or replace annual_totals rows.

    rows: list of (wikidata_id, title, year, views) tuples.
    """
    with get_connection(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO annual_totals (wikidata_id, title, year, views)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(wikidata_id, year) DO UPDATE SET
                title = excluded.title,
                views = excluded.views
            """,
            rows,
        )
        conn.commit()


def record_fetch_status(
    wikidata_id: str,
    title: str,
    status: str,
    error: str | None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """Upsert a row into fetch_log. Raises IntegrityError on invalid status."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO fetch_log (wikidata_id, title, fetched_at, status, error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(wikidata_id) DO UPDATE SET
                title = excluded.title,
                fetched_at = excluded.fetched_at,
                status = excluded.status,
                error = excluded.error
            """,
            (wikidata_id, title, now, status, error),
        )
        conn.commit()


def get_qids_needing_fetch(
    candidate_qids: set[str],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> set[str]:
    """Return the subset of candidate_qids that are NOT marked 'ok' in fetch_log.

    Used by `fetch_history.py resume` to skip already-completed fetches.
    """
    with get_connection(db_path) as conn:
        ok_rows = conn.execute(
            "SELECT wikidata_id FROM fetch_log WHERE status = 'ok'"
        ).fetchall()
    ok = {row["wikidata_id"] for row in ok_rows}
    return candidate_qids - ok
```

**Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_historical_db.py -v
```
Expected: all 5 tests pass.

**Step 5: Commit**

```bash
git add analysis/historical-decline/history_db.py tests/test_historical_db.py
git commit -m "feat(analysis): add history_db write helpers for annual totals and fetch log"
```

---

## Task 3: Async fetch_history.py — core fetcher

**Files:**
- Create: `analysis/historical-decline/fetch_history.py`
- Create: `tests/test_historical_fetch.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/test_historical_fetch.py -v
```
Expected: `ModuleNotFoundError: No module named 'fetch_history'`.

**Step 3: Implement `fetch_history.py`**

```python
"""Historical pageview fetcher.

Fetches 2016–2025 monthly pageviews for every career in careers.db from the
Wikimedia Pageviews REST API, aggregates into annual totals, and persists to
history.db.

Usage:
    uv run python analysis/historical-decline/fetch_history.py fetch
    uv run python analysis/historical-decline/fetch_history.py fetch --limit 10
    uv run python analysis/historical-decline/fetch_history.py resume
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import aiohttp

# Ensure the analysis directory is importable (history_db, pageviews_api).
sys.path.insert(0, str(Path(__file__).parent))

# Make the repo root importable so we can reach db.py for the career list.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import history_db
import pageviews_api
from db import get_database

START_YEAR = 2016
END_YEAR = 2025
DEFAULT_CONCURRENCY = 50
CHUNK_SIZE = 500  # matches fetcher.py:fetch_pageviews_batch


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def _http_get_json(
    session: aiohttp.ClientSession, url: str
) -> tuple[int, dict | None]:
    """Low-level GET used by the fetcher. Isolated so tests can monkeypatch it."""
    async with session.get(url) as response:
        if response.status == 200:
            return 200, await response.json()
        return response.status, None


async def _fetch_one(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    career: dict,
    db_path: Path,
) -> None:
    """Fetch one career's history and write to history.db."""
    qid = career["wikidata_id"]
    title = pageviews_api.extract_title_from_url(career["wikipedia_url"])
    if not title:
        history_db.record_fetch_status(qid, "", "error", "no title in url", db_path=db_path)
        return

    url = pageviews_api.build_url(title, START_YEAR, END_YEAR)

    async with semaphore:
        try:
            status, payload = await _http_get_json(session, url)
        except Exception as exc:  # noqa: BLE001 -- we want all failures logged
            history_db.record_fetch_status(qid, title, "error", str(exc), db_path=db_path)
            return

    if status == 404 or payload is None:
        history_db.record_fetch_status(qid, title, "missing", f"HTTP {status}", db_path=db_path)
        return

    items = payload.get("items", [])
    if not items:
        history_db.record_fetch_status(qid, title, "missing", "empty items", db_path=db_path)
        return

    totals = pageviews_api.sum_monthly_views_by_year(items)
    rows = [
        (qid, title, year, views)
        for year, views in sorted(totals.items())
        if START_YEAR <= year <= END_YEAR
    ]
    if rows:
        history_db.upsert_annual_totals(rows, db_path=db_path)
    history_db.record_fetch_status(qid, title, "ok", None, db_path=db_path)


async def fetch_all(
    careers: list[dict],
    db_path: Path = history_db.DEFAULT_DB_PATH,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> None:
    """Fetch pageview history for every career in the list.

    No early termination on individual failures; every failure becomes a row
    in fetch_log so `resume` can retry.
    """
    semaphore = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency)
    headers = {"User-Agent": pageviews_api.USER_AGENT}

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        total = len(careers)
        for i in range(0, total, CHUNK_SIZE):
            chunk = careers[i : i + CHUNK_SIZE]
            await asyncio.gather(
                *(_fetch_one(session, semaphore, c, db_path) for c in chunk)
            )
            done = min(i + CHUNK_SIZE, total)
            log(f"Progress: {done}/{total} ({done * 100 // total}%)")


async def resume(
    careers: list[dict],
    db_path: Path = history_db.DEFAULT_DB_PATH,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> None:
    """Fetch only careers whose fetch_log status is NOT 'ok'."""
    candidate_qids = {c["wikidata_id"] for c in careers}
    needing = history_db.get_qids_needing_fetch(candidate_qids, db_path=db_path)
    subset = [c for c in careers if c["wikidata_id"] in needing]
    log(f"Resuming: {len(subset)} of {len(careers)} careers need fetching")
    if subset:
        await fetch_all(subset, db_path=db_path, concurrency=concurrency)


def _load_careers_from_careers_db(limit: int | None = None) -> list[dict]:
    """Read wikidata_id + wikipedia_url from careers.db.

    Filters out rows missing either field (defensive; normal dataset has both).
    """
    db = get_database()
    all_rows = db.get_all_careers()
    careers = [
        {"wikidata_id": r["wikidata_id"], "wikipedia_url": r["wikipedia_url"]}
        for r in all_rows
        if r.get("wikidata_id") and r.get("wikipedia_url")
    ]
    if limit:
        careers = careers[:limit]
    return careers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="Fetch all careers from scratch")
    p_fetch.add_argument("--limit", type=int, default=None, help="Limit for testing")
    p_fetch.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)

    p_resume = sub.add_parser("resume", help="Fetch only incomplete/errored rows")
    p_resume.add_argument("--limit", type=int, default=None)
    p_resume.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)

    args = parser.parse_args()

    # Ensure schema exists before writing.
    history_db.init_schema()

    careers = _load_careers_from_careers_db(limit=args.limit)
    log(f"Loaded {len(careers)} careers from careers.db")

    if args.cmd == "fetch":
        asyncio.run(fetch_all(careers, concurrency=args.concurrency))
    elif args.cmd == "resume":
        asyncio.run(resume(careers, concurrency=args.concurrency))
    else:  # argparse makes this unreachable
        parser.error(f"unknown command: {args.cmd}")

    log("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_historical_fetch.py -v
```
Expected: all 5 test methods in `TestFetchHistoryEndToEnd` and `TestResume` pass.

**Step 5: Smoke test against the real API with a small slice**

This step also serves as our User-Agent verification: if the header is malformed or non-compliant, Wikimedia returns HTTP 403 and every row lands as `missing` / `error`. A successful real-API run is the proof that the User-Agent is accepted.

```bash
cd /var/home/louie/Projects/Volunteering-Consulting/wikipedia-career-images
uv run python analysis/historical-decline/fetch_history.py fetch --limit 3
```

Expected:
- Log output showing `Loaded N careers` and `Progress: 3/3 (100%)`.
- Script exits 0.
- Inspect results:

```bash
sqlite3 analysis/historical-decline/history.db \
    "SELECT wikidata_id, year, views FROM annual_totals ORDER BY wikidata_id, year;"
```
Expected: up to 30 rows (3 careers × 10 years). Fewer rows are acceptable if some careers have titles that drifted since 2016 — those will appear as `missing` in `fetch_log` rather than producing annual rows. That is correct behavior, not a bug.

```bash
sqlite3 analysis/historical-decline/history.db \
    "SELECT wikidata_id, status FROM fetch_log;"
```
Expected: 3 rows, status values are a mix of `ok` and possibly `missing`. If ANY row has `status = 'error'` with a 403-related message in the `error` column, the User-Agent is being rejected — STOP and fix the User-Agent string in `pageviews_api.USER_AGENT` before proceeding.

**Step 6: Commit**

```bash
git add analysis/historical-decline/fetch_history.py tests/test_historical_fetch.py
git commit -m "feat(analysis): add async historical pageview fetcher with resume"
```

---

## Phase 2 Done Criteria

All of the following must be true before proceeding to Phase 3:

- `uv run pytest tests/test_historical_pageviews_api.py tests/test_historical_db.py tests/test_historical_fetch.py -v` passes with zero failures.
- `uv run python analysis/historical-decline/fetch_history.py fetch --limit 3` runs successfully and writes 3 rows to `fetch_log` with status values in `{ok, missing}` only (NO `error` rows — an `error` row indicates User-Agent rejection, network failure, or other real-world breakage that must be diagnosed).
- `annual_totals` contains at least one row per `ok` career in `fetch_log`, with the expected 10-year coverage (2016–2025).
- Killing a mid-run fetch (Ctrl-C) followed by `resume` re-fetches only the incomplete rows. Verify manually: run `fetch --limit 20`, kill partway, then `resume`, confirming from `fetch_log` that only non-`ok` rows were re-processed.
- Full existing test suite still passes: `uv run pytest tests/ -v`.
