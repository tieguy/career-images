"""Per-(QID, language) Wikimedia pageview fetcher for cross-language comparison.

Keyed on QID so de.wikipedia's "Hund" and en.wikipedia's "Dog" line up for the
same Wikidata item. The en.wikipedia data stays in monthly_views (title-keyed);
this script writes to cross_lang_monthly_views (QID + language keyed).

Sequential, same as the other fetchers in this repo. Resumable via
cross_lang_fetch_log: (qid, language) pairs with status='ok' are skipped on
re-run.

Usage:
    uv run python analysis/vital-articles/fetch_cross_lang_pageviews.py fetch --language es
    uv run python analysis/vital-articles/fetch_cross_lang_pageviews.py fetch --language es --limit 10
    uv run python analysis/vital-articles/fetch_cross_lang_pageviews.py resume --language es
    uv run python analysis/vital-articles/fetch_cross_lang_pageviews.py fetch --all-viable
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "career-cliff"))

import pageviews_api  # noqa: E402  -- we use extract_monthly_views only
import vital_db  # noqa: E402

USER_AGENT = (
    "WikipediaCareerImages-VitalArticles/1.0 "
    "(User:LuisVilla; https://en.wikipedia.org/wiki/User:LuisVilla/ImageDiversityTool)"
)
START_YEAR = 2016
END_YEAR = 2026
END_MONTH = 3
DEFAULT_DELAY = 0.1
PROGRESS_EVERY = 200
MAX_RETRIES = 3
REQUEST_TIMEOUT = 60

# Determined by coverage_report.py at the 80% viability threshold on the
# current 5,000-article sample. Excludes 'en' (already fetched title-keyed
# into monthly_views).
VIABLE_LANGUAGES = ["es", "fr", "de", "zh", "ru", "it", "ar", "pt", "fa", "ja", "uk"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _build_url(project: str, title: str) -> str:
    encoded = quote(title, safe="")
    start = f"{START_YEAR}010100"
    end = f"{END_YEAR}{END_MONTH:02d}3100"
    return (
        f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"{project}/all-access/user/{encoded}/monthly/{start}/{end}"
    )


def _http_get_json(
    session: requests.Session, url: str
) -> tuple[int, dict | None]:
    """GET with retry/backoff on 429, 5xx, transient network errors."""
    last_status: int | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            last_status = r.status_code
            if r.status_code == 200:
                return 200, r.json()
            if r.status_code == 404:
                return 404, None
            if r.status_code == 429 or 500 <= r.status_code < 600:
                backoff = 2 ** attempt
                time.sleep(backoff)
                continue
            r.raise_for_status()
        except requests.RequestException:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2 ** attempt)
    return last_status or 0, None


def _load_targets(language: str, refetch: bool) -> list[tuple[str, str]]:
    """Return [(qid, foreign_title), ...] for the given language's sample."""
    if refetch:
        query = """
            SELECT sl.qid, sl.foreign_title
            FROM sitelinks sl
            JOIN samples s ON s.wikidata_id = sl.qid
            WHERE sl.language = ?
            ORDER BY sl.qid
        """
        params = (language,)
    else:
        query = """
            SELECT sl.qid, sl.foreign_title
            FROM sitelinks sl
            JOIN samples s ON s.wikidata_id = sl.qid
            LEFT JOIN cross_lang_fetch_log lg
              ON lg.qid = sl.qid AND lg.language = sl.language
            WHERE sl.language = ?
              AND (lg.status IS NULL OR lg.status != 'ok')
            ORDER BY sl.qid
        """
        params = (language,)
    with vital_db.get_connection() as conn:
        return [(r["qid"], r["foreign_title"])
                for r in conn.execute(query, params).fetchall()]


def _record_status(
    qid: str, language: str, status: str, error: str | None
) -> None:
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    with vital_db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO cross_lang_fetch_log (qid, language, fetched_at, status, error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(qid, language) DO UPDATE SET
                fetched_at = excluded.fetched_at,
                status = excluded.status,
                error = excluded.error
            """,
            (qid, language, fetched_at, status, error),
        )
        conn.commit()


def _upsert_monthly(
    qid: str, language: str, monthly: list[tuple[int, int, int]]
) -> None:
    if not monthly:
        return
    rows = [(qid, language, y, m, v) for (y, m, v) in monthly]
    with vital_db.get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO cross_lang_monthly_views (qid, language, year, month, views)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(qid, language, year, month) DO UPDATE SET
                views = excluded.views
            """,
            rows,
        )
        conn.commit()


def _fetch_one(
    session: requests.Session, qid: str, language: str, title: str
) -> None:
    project = f"{language}.wikipedia"
    url = _build_url(project, title)
    try:
        status, payload = _http_get_json(session, url)
    except requests.RequestException as e:
        _record_status(qid, language, "error", str(e))
        return
    if status == 404 or payload is None:
        _record_status(qid, language, "missing" if status == 404 else "error",
                       None if status == 404 else f"status={status}")
        return
    monthly = pageviews_api.extract_monthly_views(payload.get("items", []))
    _upsert_monthly(qid, language, monthly)
    _record_status(qid, language, "ok", None)


def fetch_language(
    language: str, delay: float, refetch: bool, limit: int | None
) -> None:
    targets = _load_targets(language, refetch)
    if limit is not None:
        targets = targets[:limit]
    if not targets:
        log(f"[{language}] nothing to do.")
        return

    log(f"[{language}] fetching {len(targets):,} (qid, title) pairs")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        total = len(targets)
        for i, (qid, title) in enumerate(targets, 1):
            _fetch_one(session, qid, language, title)
            if i % PROGRESS_EVERY == 0 or i == total:
                log(f"[{language}] Progress: {i}/{total} ({i * 100 // total}%)")
            if delay > 0 and i < total:
                time.sleep(delay)
    finally:
        session.close()
    log(f"[{language}] Done.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("fetch", "resume"):
        p = sub.add_parser(name)
        grp = p.add_mutually_exclusive_group(required=True)
        grp.add_argument("--language", help="Single wiki language code (e.g. es, de)")
        grp.add_argument("--all-viable", action="store_true",
                         help=f"Fetch all {len(VIABLE_LANGUAGES)} non-en viable languages in sequence")
        p.add_argument("--delay", type=float, default=DEFAULT_DELAY)
        p.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    vital_db.init_schema()

    refetch = (args.cmd == "fetch")
    languages = VIABLE_LANGUAGES if args.all_viable else [args.language]
    for language in languages:
        fetch_language(language, args.delay, refetch, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
