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
