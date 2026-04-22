"""Fetch XTools articleinfo for the sampled articles.

One call per article to https://xtools.wmcloud.org/api/page/articleinfo/
{project}/{title}. Provides total revisions, unique editors, watchers,
anon/minor edit counts, and article creation date — the inputs we want
for a multivariate regression against pageview decline.

XTools is a community-run tool on wmcloud; response times run ~1-4s per
article (they do on-demand aggregation over the full revision history).
Keep delay modest and sequential. Resumable: 'ok' rows are skipped.

Scoped to en only by default — the multivariate question is en-first.
Use --language to run against another wiki (foreign_title lookup via
sitelinks); XTools supports all WMF wikis.

Usage:
    uv run python analysis/vital-articles/fetch_xtools.py
    uv run python analysis/vital-articles/fetch_xtools.py --limit 10
    uv run python analysis/vital-articles/fetch_xtools.py --language es
    uv run python analysis/vital-articles/fetch_xtools.py --refetch
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

sys.path.insert(0, str(Path(__file__).parent))

import vital_db  # noqa: E402

USER_AGENT = (
    "WikipediaCareerImages-VitalArticles/1.0 "
    "(User:LuisVilla; https://en.wikipedia.org/wiki/User:LuisVilla/ImageDiversityTool)"
)
XTOOLS_BASE = "https://xtools.wmcloud.org/api/page/articleinfo"
DEFAULT_DELAY = 0.5
PROGRESS_EVERY = 100
MAX_RETRIES = 3
REQUEST_TIMEOUT = 120

# Same viable set as fetch_cross_lang_pageviews.py, plus en for completeness.
ALL_VIABLE = ["en", "es", "fr", "de", "zh", "ru", "it", "ar", "pt", "fa", "ja", "uk"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_targets(language: str, refetch: bool) -> list[tuple[str, str]]:
    """Return [(qid, title), ...] for the given language."""
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
                NOT IN (SELECT qid, language FROM article_stats WHERE status = 'ok')
        """
        params = (*params, language)
    query += " ORDER BY 1"
    with vital_db.get_connection() as conn:
        return [(r["qid"], r["foreign_title"])
                for r in conn.execute(query, params).fetchall()]


def _write_row(
    qid: str, language: str, status: str,
    parsed: dict | None, error: str | None,
) -> None:
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    p = parsed or {}
    with vital_db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO article_stats
              (qid, language, revisions, editors, anon_edits, minor_edits,
               watchers, created_at, fetched_at, status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(qid, language) DO UPDATE SET
              revisions = excluded.revisions,
              editors = excluded.editors,
              anon_edits = excluded.anon_edits,
              minor_edits = excluded.minor_edits,
              watchers = excluded.watchers,
              created_at = excluded.created_at,
              fetched_at = excluded.fetched_at,
              status = excluded.status,
              error = excluded.error
            """,
            (
                qid, language,
                p.get("revisions"), p.get("editors"),
                p.get("anon_edits"), p.get("minor_edits"),
                p.get("watchers"), p.get("created_at"),
                fetched_at, status, error,
            ),
        )
        conn.commit()


def _fetch_one(
    session: requests.Session, language: str, title: str,
) -> tuple[str, dict | None, str | None]:
    project = f"{language}.wikipedia.org"
    url = f"{XTOOLS_BASE}/{project}/{quote(title, safe='')}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                return "error", None, str(e)
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 404:
            return "missing", None, None
        if r.status_code == 429 or 500 <= r.status_code < 600:
            if attempt == MAX_RETRIES:
                return "error", None, f"status={r.status_code}"
            time.sleep(2 ** attempt)
            continue
        if r.status_code != 200:
            return "error", None, f"status={r.status_code}: {r.text[:200]}"
        try:
            data = r.json()
        except ValueError as e:
            return "error", None, f"invalid json: {e}"
        if "error" in data:
            return "missing", None, str(data["error"])[:200]
        return "ok", data, None
    return "error", None, "retries exhausted"


def run(language: str, delay: float, refetch: bool, limit: int | None) -> None:
    targets = _load_targets(language, refetch)
    if limit is not None:
        targets = targets[:limit]
    if not targets:
        log(f"[{language}] nothing to do.")
        return

    log(f"[{language}] fetching XTools articleinfo for {len(targets):,} articles")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        total = len(targets)
        ok_n = missing_n = err_n = 0
        for i, (qid, title) in enumerate(targets, 1):
            status, parsed, err = _fetch_one(session, language, title)
            _write_row(qid, language, status, parsed, err)
            if status == "ok":
                ok_n += 1
            elif status == "missing":
                missing_n += 1
            else:
                err_n += 1
            if i % PROGRESS_EVERY == 0 or i == total:
                log(f"[{language}] progress: {i}/{total} ({i * 100 // total}%)  "
                    f"ok={ok_n:,}  missing={missing_n:,}  error={err_n:,}")
            if delay > 0 and i < total:
                time.sleep(delay)
    finally:
        session.close()
    log(f"[{language}] Done. ok={ok_n:,}  missing={missing_n:,}  error={err_n:,}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--language", help="Single wiki language code (default: en)")
    grp.add_argument("--all-viable", action="store_true",
                     help=f"Run sequentially for every language in {ALL_VIABLE}")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--refetch", action="store_true",
                        help="Re-fetch rows already stored as 'ok'")
    args = parser.parse_args()

    vital_db.init_schema()
    languages = ALL_VIABLE if args.all_viable else [args.language or "en"]
    for language in languages:
        run(language, args.delay, args.refetch, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
