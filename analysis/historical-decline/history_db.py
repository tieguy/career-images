"""SQLite wrapper for the historical pageview decline subproject.

Kept deliberately minimal and separate from the live app's db.py (which juggles
SQLite vs MariaDB for Toolforge). This subproject is SQLite-only.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path(__file__).parent / "history.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection with Row factory. Caller is responsible for closing."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """Context-managed connection, mirroring db.py's pattern."""
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_schema(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Apply schema.sql to the given database. Idempotent."""
    schema = SCHEMA_PATH.read_text()
    with get_connection(db_path) as conn:
        conn.executescript(schema)
        conn.commit()


def table_names(db_path: Path | str = DEFAULT_DB_PATH) -> list[str]:
    """Return the list of user table names in the database."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return [r["name"] for r in rows]


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
