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
import random
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
MAX_RETRIES = 3


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def _http_get_raw(
    session: aiohttp.ClientSession, url: str
) -> tuple[int, dict | None, dict | None]:
    """Low-level GET without retry. Returns (status, body, response_headers).

    Isolated so tests can monkeypatch it. Response headers are needed for Retry-After.
    """
    async with session.get(url) as response:
        headers = dict(response.headers) if response.headers else {}
        if response.status == 200:
            return 200, await response.json(), headers
        return response.status, None, headers


async def _http_get_json(
    session: aiohttp.ClientSession, url: str
) -> tuple[int, dict | None]:
    """GET with Wikimedia-compliant retry/backoff on 429, 5xx, and network errors."""
    last_status = None
    last_body = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            status, body, headers = await _http_get_raw(session, url)
            last_status = status
            last_body = body

            # On success, return immediately.
            if status == 200:
                return status, body

            # On 429 or 5xx, check Retry-After or use exponential backoff.
            if status == 429 or (500 <= status < 600):
                if attempt < MAX_RETRIES:
                    # Try to parse Retry-After header as seconds (float or int).
                    wait_seconds = None
                    if "retry-after" in headers:
                        try:
                            wait_seconds = float(headers["retry-after"])
                        except (ValueError, TypeError):
                            pass

                    # Fall back to exponential backoff if Retry-After not parseable.
                    if wait_seconds is None:
                        wait_seconds = 2 ** attempt + random.uniform(0, 1)

                    log(
                        f"retry {attempt + 1}/{MAX_RETRIES} for {url} after {status} "
                        f"(sleeping {wait_seconds:.1f}s)"
                    )
                    await asyncio.sleep(wait_seconds)
                    continue
            # Non-retriable status or exhausted retries.
            return status, body

        except aiohttp.ClientError as exc:
            # Network errors: retry with exponential backoff.
            if attempt < MAX_RETRIES:
                wait_seconds = 2 ** attempt + random.uniform(0, 1)
                log(
                    f"retry {attempt + 1}/{MAX_RETRIES} for {url} after "
                    f"{type(exc).__name__} (sleeping {wait_seconds:.1f}s)"
                )
                await asyncio.sleep(wait_seconds)
                continue
            # Exhausted retries; raise to caller's exception handler.
            raise

    # Return the last status/body we got (should be unreachable in normal flow).
    return last_status, last_body


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

    # Treat all-zero pageviews as missing (title drift heuristic).
    if totals and sum(totals.values()) == 0:
        history_db.record_fetch_status(qid, title, "missing", "all-zero views", db_path=db_path)
        return

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
    # Double concurrency limit (semaphore + connector) mirrors fetcher.py:fetch_pageviews_batch convention.
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
    if limit is not None:
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
