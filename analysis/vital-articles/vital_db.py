"""SQLite wrapper for the Vital Articles pageview analysis subproject.

Parallel to career-cliff/history_db.py; kept separate so this subproject owns
its own database file (vital.db) and doesn't share state with career-cliff.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path(__file__).parent / "vital.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    # timeout: wait up to 30s for concurrent writers instead of failing with
    # "database is locked" (multi-process fetchers hit this under load).
    # WAL journal mode is a persistent file setting; setting it here is
    # idempotent and costs effectively nothing when it's already WAL.
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_schema(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    schema = SCHEMA_PATH.read_text()
    with get_connection(db_path) as conn:
        _migrate_samples_add_wikidata_id(conn)
        conn.executescript(schema)
        conn.commit()


def _migrate_samples_add_wikidata_id(conn: sqlite3.Connection) -> None:
    """Idempotent: add samples.wikidata_id to DBs created before the column existed.

    CREATE TABLE IF NOT EXISTS silently no-ops on existing tables, so new
    columns from schema.sql won't land without an explicit ALTER. Must run
    before executescript so CREATE INDEX idx_samples_wikidata succeeds.
    Safe no-op when samples doesn't exist yet (fresh DB path).
    """
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='samples'"
    ).fetchall()}
    if "samples" not in tables:
        return
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(samples)").fetchall()}
    if "wikidata_id" not in existing:
        conn.execute("ALTER TABLE samples ADD COLUMN wikidata_id TEXT")


def table_names(db_path: Path | str = DEFAULT_DB_PATH) -> list[str]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return [r["name"] for r in rows]


def upsert_articles(
    rows: list[tuple[str, int, str]],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """Insert or update rows of (title, level, source_file)."""
    with get_connection(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO articles (title, level, source_file)
            VALUES (?, ?, ?)
            ON CONFLICT(title) DO UPDATE SET
                level = excluded.level,
                source_file = excluded.source_file
            """,
            rows,
        )
        conn.commit()


def upsert_article_topics(
    rows: list[tuple[str, str, str | None]],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """Insert rows of (title, topic, section). Duplicates are silently ignored.

    None sections are normalized to '' so the composite PK works.
    """
    normalized = [(t, topic, section or "") for (t, topic, section) in rows]
    with get_connection(db_path) as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO article_topics (title, topic, section)
            VALUES (?, ?, ?)
            """,
            normalized,
        )
        conn.commit()


def record_ingest_status(
    source_file: str,
    status: str,
    entry_count: int | None,
    error: str | None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO ingest_log (source_file, fetched_at, status, entry_count, error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_file) DO UPDATE SET
                fetched_at = excluded.fetched_at,
                status = excluded.status,
                entry_count = excluded.entry_count,
                error = excluded.error
            """,
            (source_file, now, status, entry_count, error),
        )
        conn.commit()


def upsert_monthly_views(
    rows: list[tuple[str, int, int, int]],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """Insert rows of (title, year, month, views). Replaces on conflict."""
    with get_connection(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO monthly_views (title, year, month, views)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(title, year, month) DO UPDATE SET
                views = excluded.views
            """,
            rows,
        )
        conn.commit()


def record_pageview_fetch_status(
    title: str,
    status: str,
    error: str | None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pageview_fetch_log (title, fetched_at, status, error)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(title) DO UPDATE SET
                fetched_at = excluded.fetched_at,
                status = excluded.status,
                error = excluded.error
            """,
            (title, now, status, error),
        )
        conn.commit()


def get_titles_needing_fetch(
    candidate_titles: set[str],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> set[str]:
    """Return the subset of candidate_titles that are NOT marked 'ok'."""
    with get_connection(db_path) as conn:
        ok_rows = conn.execute(
            "SELECT title FROM pageview_fetch_log WHERE status = 'ok'"
        ).fetchall()
    ok = {row["title"] for row in ok_rows}
    return candidate_titles - ok


def load_sample_titles(
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[str]:
    """Return titles from the samples table, sorted for deterministic order."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT title FROM samples ORDER BY title"
        ).fetchall()
    return [r["title"] for r in rows]


def counts(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, int]:
    """Return simple row counts for CLI feedback."""
    with get_connection(db_path) as conn:
        (articles_n,) = conn.execute(
            "SELECT COUNT(*) FROM articles"
        ).fetchone()
        (topics_n,) = conn.execute(
            "SELECT COUNT(*) FROM article_topics"
        ).fetchone()
        (level5_n,) = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE level = 5"
        ).fetchone()
    return {
        "articles": articles_n,
        "article_topics": topics_n,
        "level5_articles": level5_n,
    }
