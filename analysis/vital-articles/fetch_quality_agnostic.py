"""Fetch Lift Wing language-agnostic articlequality scalar for all sampled articles.

Uses model_name='articlequality' (not the per-wiki legacy models). POST body is
{rev_id, lang}. Response is a single 0-1 scalar score per article, comparable
across wikis. Works for every WMF wiki — no per-language 404s.

Two-stage pipeline per language:
  1. Batch MW API for current revids (titles from samples for en, from sitelinks
     for non-en).
  2. Per rev_id, POST to language-agnostic articlequality:predict.

Writes to article_quality_score(qid, language, rev_id, score, ...).
Sequential, resumable. Status='ok' rows skipped on rerun.

Usage:
    uv run python analysis/vital-articles/fetch_quality_agnostic.py --language en
    uv run python analysis/vital-articles/fetch_quality_agnostic.py --all-viable
    uv run python analysis/vital-articles/fetch_quality_agnostic.py --language ja --limit 10
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
LIFTWING_URL = (
    "https://api.wikimedia.org/service/lw/inference/v1/"
    "models/articlequality:predict"
)
REVID_BATCH = 50
DEFAULT_DELAY = 0.1
PROGRESS_EVERY = 200
MAX_RETRIES = 3
REQUEST_TIMEOUT = 60

# Mirrors fetch_cross_lang_pageviews.VIABLE_LANGUAGES plus en.
VIABLE_LANGUAGES = ["en", "es", "fr", "de", "zh", "ru", "it", "ar", "pt", "fa", "ja", "uk"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _mw_api(language: str) -> str:
    return f"https://{language}.wikipedia.org/w/api.php"


def _load_targets(language: str, refetch: bool) -> list[tuple[str, str]]:
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
                    SELECT qid, language FROM article_quality_score WHERE status = 'ok'
                )
        """
        params = (*params, language)
    with vital_db.get_connection() as conn:
        return [(r["qid"], r["foreign_title"])
                for r in conn.execute(query, params).fetchall()]


def _fetch_revids(
    titles: list[str], language: str, session: requests.Session
) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in range(0, len(titles), REVID_BATCH):
        batch = titles[i:i + REVID_BATCH]
        params = {
            "action": "query",
            "prop": "revisions",
            "rvprop": "ids",
            "titles": "|".join(batch),
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
                    log(f"  [{language}] revid batch failed: {e}")
                    data = None
                    break
                time.sleep(2 ** attempt)
        if not data:
            continue

        rename: dict[str, str] = {}
        for entry in data.get("query", {}).get("normalized", []):
            rename[entry["from"]] = entry["to"]
        for entry in data.get("query", {}).get("redirects", []):
            rename[entry["from"]] = entry["to"]
        page_by_title: dict[str, dict] = {}
        for page in data.get("query", {}).get("pages", []):
            page_by_title[page["title"]] = page

        for input_title in batch:
            canonical = rename.get(input_title, input_title)
            page = page_by_title.get(canonical)
            if not page or "revisions" not in page or not page["revisions"]:
                continue
            out[input_title] = page["revisions"][0]["revid"]
        time.sleep(0.1)
    return out


def _fetch_score_one(
    session: requests.Session, language: str, rev_id: int
) -> tuple[str, float | None, str | None]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.post(
                LIFTWING_URL,
                json={"rev_id": rev_id, "lang": language},
                headers={"Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                return "http_error", None, str(e)
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429 or 500 <= r.status_code < 600:
            if attempt == MAX_RETRIES:
                return "http_error", None, f"status={r.status_code}"
            time.sleep(2 ** attempt)
            continue
        if r.status_code != 200:
            return "http_error", None, f"status={r.status_code}: {r.text[:200]}"
        try:
            payload = r.json()
        except ValueError as e:
            return "http_error", None, f"invalid json: {e}"
        score = payload.get("score")
        if score is None:
            return "model_error", None, f"no score field: {str(payload)[:200]}"
        return "ok", float(score), None
    return "http_error", None, "retries exhausted"


def _write_row(
    qid: str, language: str, rev_id: int | None, status: str,
    score: float | None, error: str | None,
) -> None:
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    with vital_db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO article_quality_score
              (qid, language, rev_id, score, fetched_at, status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(qid, language) DO UPDATE SET
              rev_id = excluded.rev_id,
              score = excluded.score,
              fetched_at = excluded.fetched_at,
              status = excluded.status,
              error = excluded.error
            """,
            (qid, language, rev_id, score, fetched_at, status, error),
        )
        conn.commit()


def run_language(language: str, delay: float, refetch: bool, limit: int | None) -> None:
    targets = _load_targets(language, refetch)
    if limit is not None:
        targets = targets[:limit]
    if not targets:
        log(f"[{language}] nothing to do.")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        log(f"[{language}] Stage 1: resolving {len(targets):,} titles → revids")
        title_to_qid = {title: qid for (qid, title) in targets}
        revid_map = _fetch_revids(list(title_to_qid.keys()), language, session)
        log(f"[{language}]   resolved {len(revid_map):,}/{len(targets):,}")

        for title, qid in title_to_qid.items():
            if title not in revid_map:
                _write_row(qid, language, None, "missing_revid", None, None)

        resolved = [(qid, revid_map[title])
                    for (title, qid) in title_to_qid.items() if title in revid_map]
        log(f"[{language}] Stage 2: scoring {len(resolved):,} articles")
        total = len(resolved)
        ok_n = 0
        for i, (qid, rev_id) in enumerate(resolved, 1):
            status, score, err = _fetch_score_one(session, language, rev_id)
            _write_row(qid, language, rev_id, status, score, err)
            if status == "ok":
                ok_n += 1
            if i % PROGRESS_EVERY == 0 or i == total:
                log(f"[{language}]   progress: {i}/{total} ({i * 100 // total}%)  ok={ok_n:,}")
            if delay > 0 and i < total:
                time.sleep(delay)
    finally:
        session.close()
    log(f"[{language}] Done. ok={ok_n:,}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--language", help=f"One of: {', '.join(VIABLE_LANGUAGES)}")
    grp.add_argument("--all-viable", action="store_true")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--refetch", action="store_true")
    args = parser.parse_args()

    vital_db.init_schema()
    languages = VIABLE_LANGUAGES if args.all_viable else [args.language]
    for language in languages:
        run_language(language, args.delay, args.refetch, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
