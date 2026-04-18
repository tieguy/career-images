"""One-shot initializer for history.db.

Usage:
    uv run python analysis/historical-decline/init_db.py [--db PATH]

Idempotent: running twice is a no-op because schema uses CREATE TABLE IF NOT EXISTS.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from history_db import DEFAULT_DB_PATH, init_schema, table_names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Path to history.db (default: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()

    init_schema(args.db)
    tables = table_names(args.db)
    print(f"Initialized {args.db}")
    print(f"Tables: {', '.join(tables)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
