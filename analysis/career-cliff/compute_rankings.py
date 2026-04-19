"""CLI for ranking + ever-top computation.

Usage:
    uv run python analysis/career-cliff/compute_rankings.py
    uv run python analysis/career-cliff/compute_rankings.py --top-n 25

Prereq: fetch_history.py has already populated annual_totals.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import history_db
import rankings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--top-n",
        type=int,
        default=50,
        help="Top-N threshold for ever-top membership (default: 50)",
    )
    parser.add_argument(
        "--db", type=Path, default=history_db.DEFAULT_DB_PATH,
    )
    args = parser.parse_args()

    if args.top_n < 1:
        parser.error("--top-n must be >= 1")

    # Guard: annual_totals must be non-empty.
    with history_db.get_connection(args.db) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM annual_totals").fetchone()
    if count == 0:
        print(
            "annual_totals is empty. Run fetch_history.py first.",
            file=sys.stderr,
        )
        return 1

    print(f"Computing ranks over {count} annual-total rows...")
    rankings.compute_ranks(db_path=args.db)

    print(f"Computing ever-top (N={args.top_n})...")
    rankings.compute_ever_top(top_n=args.top_n, db_path=args.db)

    with history_db.get_connection(args.db) as conn:
        (ever_top_count,) = conn.execute("SELECT COUNT(*) FROM ever_top").fetchone()
    print(f"ever_top populated with {ever_top_count} articles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
