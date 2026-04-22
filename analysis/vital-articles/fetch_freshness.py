"""Fetch each sampled article's current revision timestamp (freshness proxy).

Addresses the hypothesis: does staleness (days since last edit) correlate
with pageview decline? One revision-timestamp per (QID, language) pair via
each wiki's MediaWiki Action API. Stored in article_freshness and keyed on
QID so en and non-en rows stack in one table.

MW API's batch of 50 titles per request makes this fast: ~100 requests to
cover 5,000 articles per language, ~1 minute per wiki.

Usage:
    uv run python analysis/vital-articles/fetch_freshness.py --language en
    uv run python analysis/vital-articles/fetch_freshness.py --all-viable
    uv run python analysis/vital-articles/fetch_freshness.py --language es --refetch
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

import vital_db  # noqa: E402

USER_AGENT = (
    "WikipediaCareerImages-VitalArticles/1.0 "
    "(User:LuisVilla; https://en.wikipedia.org/wiki/User:LuisVilla/ImageDiversityTool)"
)
TITLE_BATCH = 50
DEFAULT_DELAY = 0.2
PROGRESS_EVERY_BATCHES = 25
MAX_RETRIES = 3
REQUEST_TIMEOUT = 60

VIABLE_LANGUAGES = ["en", "es", "fr", "de", "zh", "ru", "it", "ar", "pt", "fa", "ja", "uk"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _mw_api(language: str) -> str:
    return f"https://{language}.wikipedia.org/w/api.php"


def _load_targets(language: str, refetch: bool) -> list[tuple[str, str]]:
    """Return [(qid, foreign_title), ...] for a language's sampled articles.

    For en: samples.wikidata_id + samples.title.
    For others: sitelinks join on resolved samples.
    """
    if language == "en":
        query = """
            SELECT s.wikidata_id AS qid, s.title AS foreign_title
            FROM samples s
            WHERE s.wikidata_id IS NOT NULL
        """
        params: tuple = ()
    else:
        query = """
            SELECT sl.qid, sl.foreign_title
            FROM sitelinks sl
            JOIN samples s ON s.wikidata_id = sl.qid
            WHERE sl.language = ?
        """
        params = (language,)

    if not refetch:
        query += f"""
            AND ({'s.wikidata_id' if language == 'en' else 'sl.qid'}, ?)
                NOT IN (
                    SELECT qid, language FROM article_freshness WHERE status = 'ok'
                )
        """
        params = (*params, language)
    query += " ORDER BY 1"

    with vital_db.get_connection() as conn:
        return [(r["qid"], r["foreign_title"])
                for r in conn.execute(query, params).fetchall()]


def _write_rows(rows: list[tuple[str, str, int | None, str | None, str, str | None]]) -> None:
    if not rows:
        return
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    payload = [
        (qid, lang, rev_id, ts, fetched_at, status, err)
        for (qid, lang, rev_id, ts, status, err) in rows
    ]
    with vital_db.get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO article_freshness
              (qid, language, rev_id, rev_timestamp, fetched_at, status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(qid, language) DO UPDATE SET
              rev_id = excluded.rev_id,
              rev_timestamp = excluded.rev_timestamp,
              fetched_at = excluded.fetched_at,
              status = excluded.status,
              error = excluded.error
            """,
            payload,
        )
        conn.commit()


def _fetch_batch(
    titles: list[str], language: str, session: requests.Session
) -> dict[str, tuple[int | None, str | None]]:
    """Return {title: (rev_id, timestamp)} for a batch. Missing titles omitted."""
    params = {
        "action": "query",
        "prop": "revisions",
        "rvprop": "ids|timestamp",
        "titles": "|".join(titles),
        "redirects": 1,
        "format": "json",
        "formatversion": 2,
    }
    data = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(_mw_api(language), params=params, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            break
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                log(f"  [{language}] batch failed after retries: {e}")
                return {}
            time.sleep(2 ** attempt)
    if not data:
        return {}

    rename: dict[str, str] = {}
    for entry in data.get("query", {}).get("normalized", []):
        rename[entry["from"]] = entry["to"]
    for entry in data.get("query", {}).get("redirects", []):
        rename[entry["from"]] = entry["to"]
    page_by_title: dict[str, dict] = {}
    for page in data.get("query", {}).get("pages", []):
        page_by_title[page["title"]] = page

    out: dict[str, tuple[int | None, str | None]] = {}
    for input_title in titles:
        canonical = rename.get(input_title, input_title)
        page = page_by_title.get(canonical)
        if not page or "revisions" not in page or not page["revisions"]:
            continue
        rev = page["revisions"][0]
        out[input_title] = (rev.get("revid"), rev.get("timestamp"))
    return out


def fetch_language(language: str, delay: float, refetch: bool) -> None:
    targets = _load_targets(language, refetch)
    if not targets:
        log(f"[{language}] nothing to do.")
        return

    log(f"[{language}] fetching freshness for {len(targets):,} articles")
    title_to_qid = {title: qid for (qid, title) in targets}
    titles = list(title_to_qid.keys())

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    ok_n = missing_n = 0
    try:
        total_batches = (len(titles) + TITLE_BATCH - 1) // TITLE_BATCH
        for bi, i in enumerate(range(0, len(titles), TITLE_BATCH), 1):
            batch = titles[i:i + TITLE_BATCH]
            mapping = _fetch_batch(batch, language, session)
            rows: list[tuple[str, str, int | None, str | None, str, str | None]] = []
            for title in batch:
                qid = title_to_qid[title]
                if title in mapping:
                    rev_id, ts = mapping[title]
                    rows.append((qid, language, rev_id, ts, "ok", None))
                    ok_n += 1
                else:
                    rows.append((qid, language, None, None, "missing", None))
                    missing_n += 1
            _write_rows(rows)
            if bi % PROGRESS_EVERY_BATCHES == 0 or bi == total_batches:
                log(f"  [{language}] batch {bi}/{total_batches}  "
                    f"ok={ok_n:,}  missing={missing_n:,}")
            if delay > 0 and i + TITLE_BATCH < len(titles):
                time.sleep(delay)
    finally:
        session.close()
    log(f"[{language}] Done. ok={ok_n:,}  missing={missing_n:,}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--language", help="Single wiki language code (e.g. en, es)")
    grp.add_argument("--all-viable", action="store_true",
                     help="Fetch freshness for all 12 viable languages in sequence")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--refetch", action="store_true",
                        help="Re-fetch rows already stored as 'ok'")
    args = parser.parse_args()

    vital_db.init_schema()

    languages = VIABLE_LANGUAGES if args.all_viable else [args.language]
    for language in languages:
        fetch_language(language, args.delay, args.refetch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
