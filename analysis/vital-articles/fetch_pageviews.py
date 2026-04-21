"""Pageview fetcher for the sampled Vital Articles (Level 5).

Parallel to career-cliff/fetch_history.py, but keyed on title instead of
Wikidata QID (our upstream data is title-keyed on-wiki JSON). Imports the
URL-construction and response-parsing helpers from the career-cliff
subproject so there is only one implementation of those pure functions.

Sequential by design — same rationale as career-cliff: concurrent fetching
triggered Wikimedia rate limits in the past.

Usage:
    uv run python analysis/vital-articles/sample.py          # draw the sample first
    uv run python analysis/vital-articles/fetch_pageviews.py fetch
    uv run python analysis/vital-articles/fetch_pageviews.py resume
    uv run python analysis/vital-articles/fetch_pageviews.py fetch --limit 10
    uv run python analysis/vital-articles/fetch_pageviews.py fetch --delay 0.2
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "career-cliff"))

import pageviews_api  # noqa: E402 -- from career-cliff/
import vital_db  # noqa: E402

USER_AGENT = (
    "WikipediaCareerImages-VitalArticles/1.0 "
    "(User:LuisVilla; https://en.wikipedia.org/wiki/User:LuisVilla/ImageDiversityTool)"
)

START_YEAR = 2016
END_YEAR = 2026
END_MONTH = 3  # through Q1 2026, mirrors career-cliff
DEFAULT_DELAY = 0.1
PROGRESS_EVERY = 100
MAX_RETRIES = 3


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _http_get_json(
    session: requests.Session, url: str
) -> tuple[int, dict | None]:
    """GET with Wikimedia-compliant retry/backoff on 429, 5xx, network errors.

    Structurally identical to career-cliff/fetch_history._http_get_json.
    Duplicated rather than imported so private retry details can diverge
    independently between subprojects.
    """
    last_status: int | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=30)
            headers = dict(response.headers) if response.headers else {}
            last_status = response.status_code
            if response.status_code == 200:
                return 200, response.json()
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < MAX_RETRIES:
                    wait = None
                    ra = next(
                        (v for k, v in headers.items() if k.lower() == "retry-after"),
                        None,
                    )
                    if ra is not None:
                        try:
                            wait = float(ra)
                        except (TypeError, ValueError):
                            wait = None
                    if wait is None:
                        wait = 2 ** attempt + random.uniform(0, 1)
                    log(f"retry {attempt + 1}/{MAX_RETRIES} for {url} after "
                        f"{response.status_code} (sleep {wait:.1f}s)")
                    time.sleep(wait)
                    continue
            return response.status_code, None
        except requests.RequestException as exc:
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt + random.uniform(0, 1)
                log(f"retry {attempt + 1}/{MAX_RETRIES} for {url} after "
                    f"{type(exc).__name__} (sleep {wait:.1f}s)")
                time.sleep(wait)
                continue
            raise
    return (last_status or 0), None


def _fetch_one(
    session: requests.Session,
    title: str,
    db_path: Path,
) -> None:
    # Pageviews API expects underscore-form. On-wiki JSON stores space-form.
    api_title = title.replace(" ", "_")
    url = pageviews_api.build_url(api_title, START_YEAR, END_YEAR, end_month=END_MONTH)

    try:
        status, payload = _http_get_json(session, url)
    except Exception as exc:  # noqa: BLE001 — all failures get logged
        vital_db.record_pageview_fetch_status(title, "error", str(exc), db_path=db_path)
        return

    if status == 404 or payload is None:
        vital_db.record_pageview_fetch_status(title, "missing", f"HTTP {status}",
                                              db_path=db_path)
        return

    items = payload.get("items", [])
    if not items:
        vital_db.record_pageview_fetch_status(title, "missing", "empty items",
                                              db_path=db_path)
        return

    monthly = pageviews_api.extract_monthly_views(items)
    if monthly and sum(v for _, _, v in monthly) == 0:
        vital_db.record_pageview_fetch_status(title, "missing", "all-zero views",
                                              db_path=db_path)
        return

    rows = [(title, y, m, v) for (y, m, v) in monthly]
    if rows:
        vital_db.upsert_monthly_views(rows, db_path=db_path)
    vital_db.record_pageview_fetch_status(title, "ok", None, db_path=db_path)


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def fetch_all(
    titles: list[str],
    db_path: Path = vital_db.DEFAULT_DB_PATH,
    delay: float = DEFAULT_DELAY,
) -> None:
    total = len(titles)
    session = _make_session()
    try:
        for i, title in enumerate(titles, 1):
            _fetch_one(session, title, db_path)
            if i % PROGRESS_EVERY == 0 or i == total:
                log(f"Progress: {i}/{total} ({i * 100 // total}%)")
            if delay > 0 and i < total:
                time.sleep(delay)
    finally:
        session.close()


def resume(
    titles: list[str],
    db_path: Path = vital_db.DEFAULT_DB_PATH,
    delay: float = DEFAULT_DELAY,
) -> None:
    needing = vital_db.get_titles_needing_fetch(set(titles), db_path=db_path)
    subset = [t for t in titles if t in needing]
    log(f"Resuming: {len(subset)} of {len(titles)} titles need fetching")
    if subset:
        fetch_all(subset, db_path=db_path, delay=delay)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name, help_ in (("fetch", "Fetch pageviews for every sampled article"),
                        ("resume", "Fetch only titles not yet marked 'ok'")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--limit", type=int, default=None, help="Limit for testing")
        p.add_argument("--delay", type=float, default=DEFAULT_DELAY)
        p.add_argument("--db", type=Path, default=vital_db.DEFAULT_DB_PATH)

    args = parser.parse_args()
    vital_db.init_schema(args.db)

    titles = vital_db.load_sample_titles(args.db)
    if not titles:
        print("samples table is empty. Run sample.py first.", file=sys.stderr)
        return 1
    if args.limit is not None:
        titles = titles[: args.limit]
    log(f"Loaded {len(titles):,} sampled titles from vital.db")

    if args.cmd == "fetch":
        fetch_all(titles, db_path=args.db, delay=args.delay)
    elif args.cmd == "resume":
        resume(titles, db_path=args.db, delay=args.delay)

    log("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
