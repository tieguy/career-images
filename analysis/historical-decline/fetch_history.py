"""Historical pageview fetcher (synchronous).

Fetches 2016–2025 monthly pageviews for every career in careers.db from the
Wikimedia Pageviews REST API, aggregates into annual totals, and persists to
history.db.

Sequential by design: concurrent fetching hit Wikimedia rate limits hard and
produced a storm of false-positive `missing` rows. A single-threaded fetcher
with a small inter-request delay stays within policy and gets clean data.

Usage:
    uv run python analysis/historical-decline/fetch_history.py fetch
    uv run python analysis/historical-decline/fetch_history.py fetch --limit 10
    uv run python analysis/historical-decline/fetch_history.py resume
    uv run python analysis/historical-decline/fetch_history.py fetch --delay 0.2
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import history_db
import pageviews_api
from db import get_database

START_YEAR = 2016
END_YEAR = 2026
END_MONTH = 3  # through Q1 2026
DEFAULT_DELAY = 0.1
PROGRESS_EVERY = 100
MAX_RETRIES = 3


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _http_get_raw(
    session: requests.Session, url: str
) -> tuple[int, dict | None, dict | None]:
    """Low-level GET without retry. Returns (status, body, response_headers).

    Isolated so tests can monkeypatch it. Response headers are needed for Retry-After.
    """
    response = session.get(url, timeout=30)
    headers = dict(response.headers) if response.headers else {}
    if response.status_code == 200:
        return 200, response.json(), headers
    return response.status_code, None, headers


def _http_get_json(
    session: requests.Session, url: str
) -> tuple[int, dict | None]:
    """GET with Wikimedia-compliant retry/backoff on 429, 5xx, and network errors."""
    last_status = None
    last_body = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            status, body, headers = _http_get_raw(session, url)
            last_status = status
            last_body = body

            if status == 200:
                return status, body

            if status == 429 or (500 <= status < 600):
                if attempt < MAX_RETRIES:
                    wait_seconds = None
                    if headers and "retry-after" in {k.lower() for k in headers}:
                        ra_value = next(
                            (v for k, v in headers.items() if k.lower() == "retry-after"),
                            None,
                        )
                        try:
                            wait_seconds = float(ra_value)
                        except (ValueError, TypeError):
                            pass

                    if wait_seconds is None:
                        wait_seconds = 2 ** attempt + random.uniform(0, 1)

                    log(
                        f"retry {attempt + 1}/{MAX_RETRIES} for {url} after {status} "
                        f"(sleeping {wait_seconds:.1f}s)"
                    )
                    time.sleep(wait_seconds)
                    continue
            return status, body

        except requests.RequestException as exc:
            if attempt < MAX_RETRIES:
                wait_seconds = 2 ** attempt + random.uniform(0, 1)
                log(
                    f"retry {attempt + 1}/{MAX_RETRIES} for {url} after "
                    f"{type(exc).__name__} (sleeping {wait_seconds:.1f}s)"
                )
                time.sleep(wait_seconds)
                continue
            raise

    return last_status, last_body


def _fetch_one(
    session: requests.Session,
    career: dict,
    db_path: Path,
) -> None:
    """Fetch one career's history and write to history.db."""
    qid = career["wikidata_id"]
    title = pageviews_api.extract_title_from_url(career["wikipedia_url"])
    if not title:
        history_db.record_fetch_status(qid, "", "error", "no title in url", db_path=db_path)
        return

    url = pageviews_api.build_url(title, START_YEAR, END_YEAR, end_month=END_MONTH)

    try:
        status, payload = _http_get_json(session, url)
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

    monthly = pageviews_api.extract_monthly_views(items)
    if monthly and sum(v for _, _, v in monthly) == 0:
        history_db.record_fetch_status(qid, title, "missing", "all-zero views", db_path=db_path)
        return

    # Persist all monthly rows (including partial years like 2026).
    monthly_rows = [(qid, title, y, m, v) for (y, m, v) in monthly]
    if monthly_rows:
        history_db.upsert_monthly_views(monthly_rows, db_path=db_path)

    # Persist annual_totals only for COMPLETE years (all 12 months present).
    by_year = pageviews_api.group_by_year(monthly)
    annual_rows = [
        (qid, title, year, sum(v for _, v in months))
        for year, months in sorted(by_year.items())
        if START_YEAR <= year <= END_YEAR and len(months) == 12
    ]
    if annual_rows:
        history_db.upsert_annual_totals(annual_rows, db_path=db_path)
    history_db.record_fetch_status(qid, title, "ok", None, db_path=db_path)


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": pageviews_api.USER_AGENT})
    return session


def fetch_all(
    careers: list[dict],
    db_path: Path = history_db.DEFAULT_DB_PATH,
    delay: float = DEFAULT_DELAY,
) -> None:
    """Fetch pageview history for every career in the list, sequentially.

    `delay` is the minimum sleep between successful requests. Retry backoff is
    separate and handled inside `_http_get_json`.
    """
    total = len(careers)
    session = _make_session()
    try:
        for i, career in enumerate(careers, 1):
            _fetch_one(session, career, db_path)
            if i % PROGRESS_EVERY == 0 or i == total:
                log(f"Progress: {i}/{total} ({i * 100 // total}%)")
            if delay > 0 and i < total:
                time.sleep(delay)
    finally:
        session.close()


def resume(
    careers: list[dict],
    db_path: Path = history_db.DEFAULT_DB_PATH,
    delay: float = DEFAULT_DELAY,
) -> None:
    """Fetch only careers whose fetch_log status is NOT 'ok'."""
    candidate_qids = {c["wikidata_id"] for c in careers}
    needing = history_db.get_qids_needing_fetch(candidate_qids, db_path=db_path)
    subset = [c for c in careers if c["wikidata_id"] in needing]
    log(f"Resuming: {len(subset)} of {len(careers)} careers need fetching")
    if subset:
        fetch_all(subset, db_path=db_path, delay=delay)


def _load_careers_from_careers_db(limit: int | None = None) -> list[dict]:
    """Read wikidata_id + wikipedia_url from careers.db."""
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
    p_fetch.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Seconds to sleep between successful requests (default: {DEFAULT_DELAY})",
    )

    p_resume = sub.add_parser("resume", help="Fetch only incomplete/errored rows")
    p_resume.add_argument("--limit", type=int, default=None)
    p_resume.add_argument("--delay", type=float, default=DEFAULT_DELAY)

    args = parser.parse_args()

    history_db.init_schema()

    careers = _load_careers_from_careers_db(limit=args.limit)
    log(f"Loaded {len(careers)} careers from careers.db")

    if args.cmd == "fetch":
        fetch_all(careers, delay=args.delay)
    elif args.cmd == "resume":
        resume(careers, delay=args.delay)
    else:
        parser.error(f"unknown command: {args.cmd}")

    log("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
