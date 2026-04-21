"""Fetch the Vital Articles article→topic map from Wikipedia's on-wiki JSON.

Source: Wikipedia:Vital_articles/data/{A..Z}.json — the canonical machine-readable
version of the Vital Articles list, which is what the on-page {{Vital article}}
template reads via Module:Vital_article. Each JSON file maps article titles to
{level, topic, section} records.

Output: writes to analysis/vital-articles/vital.db (tables: articles,
article_topics, ingest_log). Idempotent; re-running upserts in place.

Usage:
    uv run python analysis/vital-articles/init_db.py           # once
    uv run python analysis/vital-articles/fetch_vital_list.py  # fetch A..Z
    uv run python analysis/vital-articles/fetch_vital_list.py --letters A B C
"""
from __future__ import annotations

import argparse
import json
import random
import string
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

RAW_URL_TEMPLATE = (
    "https://en.wikipedia.org/wiki/Wikipedia:Vital_articles/data/{letter}.json?action=raw"
)

DEFAULT_DELAY = 0.5
MAX_RETRIES = 3


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _fetch_raw(session: requests.Session, url: str) -> tuple[int, str | None]:
    """GET with retry/backoff on 429 and 5xx. Returns (status, body)."""
    for attempt in range(MAX_RETRIES + 1):
        response = session.get(url, timeout=30)
        status = response.status_code
        if status == 200:
            return status, response.text
        if status == 429 or 500 <= status < 600:
            if attempt < MAX_RETRIES:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else 2 ** attempt + random.uniform(0, 1)
                except (TypeError, ValueError):
                    wait = 2 ** attempt + random.uniform(0, 1)
                log(f"retry {attempt + 1}/{MAX_RETRIES} after HTTP {status} "
                    f"(sleep {wait:.1f}s)")
                time.sleep(wait)
                continue
        return status, None
    return status, None


def _extract_entries(payload: dict, source_file: str) -> tuple[
    list[tuple[str, int, str]],
    list[tuple[str, str, str | None]],
    int,
]:
    """Pull (title, level, source_file) and (title, topic, section) rows from one JSON.

    Expected shape:
        {"Article Title": {"level": 5, "topic": "...", "section": "..."}, ...}

    Also tolerates the list-of-dicts shape in case an article is multi-listed:
        {"Article Title": [{"level": 5, "topic": "...", ...}, {...}], ...}

    Returns (article_rows, topic_rows, skipped_count).
    """
    article_rows: list[tuple[str, int, str]] = []
    topic_rows: list[tuple[str, str, str | None]] = []
    skipped = 0

    for title, value in payload.items():
        records = value if isinstance(value, list) else [value]
        seen_level: int | None = None

        for rec in records:
            if not isinstance(rec, dict):
                skipped += 1
                continue
            level = rec.get("level")
            topic = rec.get("topic")
            section = rec.get("section")
            if not isinstance(level, int) or not isinstance(topic, str):
                skipped += 1
                continue
            # Store the lowest level seen (most vital). In practice titles
            # shouldn't span multiple levels, but be defensive.
            seen_level = level if seen_level is None else min(seen_level, level)
            topic_rows.append((title, topic, section if isinstance(section, str) else None))

        if seen_level is not None:
            article_rows.append((title, seen_level, source_file))

    return article_rows, topic_rows, skipped


def fetch_one_letter(
    session: requests.Session,
    letter: str,
    db_path: Path,
) -> None:
    source_file = f"{letter}.json"
    url = RAW_URL_TEMPLATE.format(letter=letter)
    status, body = _fetch_raw(session, url)

    if status == 404:
        vital_db.record_ingest_status(source_file, "missing", 0, f"HTTP {status}",
                                      db_path=db_path)
        log(f"{source_file}: 404 (no such page)")
        return

    if status != 200 or body is None:
        vital_db.record_ingest_status(source_file, "error", None, f"HTTP {status}",
                                      db_path=db_path)
        log(f"{source_file}: failed with HTTP {status}")
        return

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        vital_db.record_ingest_status(source_file, "error", None, f"JSON: {exc}",
                                      db_path=db_path)
        log(f"{source_file}: JSON decode error: {exc}")
        return

    if not isinstance(payload, dict):
        vital_db.record_ingest_status(source_file, "error", None,
                                      f"expected dict, got {type(payload).__name__}",
                                      db_path=db_path)
        log(f"{source_file}: unexpected top-level type")
        return

    article_rows, topic_rows, skipped = _extract_entries(payload, source_file)
    if article_rows:
        vital_db.upsert_articles(article_rows, db_path=db_path)
    if topic_rows:
        vital_db.upsert_article_topics(topic_rows, db_path=db_path)

    vital_db.record_ingest_status(source_file, "ok", len(article_rows), None,
                                  db_path=db_path)
    log(f"{source_file}: {len(article_rows)} articles, {len(topic_rows)} topic rows"
        + (f", {skipped} skipped" if skipped else ""))


def fetch_all(
    letters: list[str],
    db_path: Path = vital_db.DEFAULT_DB_PATH,
    delay: float = DEFAULT_DELAY,
) -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        for i, letter in enumerate(letters):
            fetch_one_letter(session, letter, db_path)
            if delay > 0 and i < len(letters) - 1:
                time.sleep(delay)
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--letters",
        nargs="+",
        default=list(string.ascii_uppercase),
        help="Letters to fetch (default: A..Z)",
    )
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--db", type=Path, default=vital_db.DEFAULT_DB_PATH)
    args = parser.parse_args()

    vital_db.init_schema(args.db)
    fetch_all([l.upper() for l in args.letters], db_path=args.db, delay=args.delay)

    c = vital_db.counts(args.db)
    log(f"Totals: articles={c['articles']:,}  topic_rows={c['article_topics']:,}  "
        f"level-5={c['level5_articles']:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
