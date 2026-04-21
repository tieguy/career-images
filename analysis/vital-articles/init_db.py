"""One-shot initializer for vital.db.

Usage:
    uv run python analysis/vital-articles/init_db.py [--db PATH]

Idempotent.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from vital_db import DEFAULT_DB_PATH, init_schema, table_names  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    init_schema(args.db)
    print(f"Initialized {args.db}")
    print(f"Tables: {', '.join(table_names(args.db))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
