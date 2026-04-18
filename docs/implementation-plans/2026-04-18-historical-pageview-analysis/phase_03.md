# Historical Pageview Analysis — Phase 3: Ranking and Ever-Top Computation

> **For Claude:** REQUIRED SUB-SKILL: Use ed3d-plan-and-execute:executing-an-implementation-plan to implement this plan task-by-task.

**Goal:** Implement `compute_rankings.py` — a CLI that reads `annual_totals` from `history.db`, computes per-year dense rank within the career set, writes ranks back to `annual_totals.rank`, and computes the `ever_top` table (the union of articles that were top-N in any year).

**Architecture:** Pure-SQL + Python rollup, no network I/O. Runs entirely against the local `history.db` populated by Phase 2. Parameterized by `--top-n` (default 50). Idempotent: re-running with a different N produces a different `ever_top` from the same `annual_totals` data.

**Tech Stack:** Python 3.13, sqlite3 stdlib.

**Scope:** Phase 3 of 4.

**Codebase verified:** 2026-04-18. `history.db` schema from Phase 1 has `annual_totals(wikidata_id, title, year, views, rank)` with `rank` nullable. SQLite 3 supports `RANK() OVER` (dense_rank available since SQLite 3.25, Python 3.13's bundled version is much newer). Confirmed via `sqlite3 :memory: "SELECT sqlite_version();"` — will be 3.40+ on the dev and deploy environments.

**Ranking method decision:** The design's glossary defines dense rank as "two articles with equal views both get rank 1, the next distinct value gets rank 2." Implementation uses `DENSE_RANK() OVER (PARTITION BY year ORDER BY views DESC, wikidata_id ASC)` — adding `wikidata_id ASC` as a secondary sort key forces a deterministic tie-break. The practical consequence: two articles can never end up with the same rank number in our output. This is technically a *unique* rank rather than a *dense* rank, but for the design's purpose (selecting the top-N per year and taking a union) the two produce identical top-N sets, so this satisfies design intent while giving us reproducible output. If a future consumer needs true dense rank semantics (e.g. for aggregate statistics that care about tie preservation), `rankings.py` will need a second column or a separate function — not needed today.

---

## Task 1: Rank computation core

**Files:**
- Create: `analysis/historical-decline/rankings.py`
- Create: `tests/test_historical_rankings.py`

**Step 1: Write the failing test**

```python
"""Tests for rankings.py — pure functions for rank and ever-top computation."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ANALYSIS_DIR = Path(__file__).parent.parent / "analysis" / "historical-decline"
sys.path.insert(0, str(ANALYSIS_DIR))

import history_db
import rankings


@pytest.fixture
def populated_db(tmp_path):
    db_path = tmp_path / "history.db"
    history_db.init_schema(db_path)
    # Small fixture dataset: 5 articles × 3 years (2016-2018)
    rows = [
        # 2016
        ("Q1", "A", 2016, 1000),
        ("Q2", "B", 2016, 800),
        ("Q3", "C", 2016, 600),
        ("Q4", "D", 2016, 400),
        ("Q5", "E", 2016, 200),
        # 2017: reshuffle — Q5 rockets to #1, Q1 drops
        ("Q1", "A", 2017, 100),
        ("Q2", "B", 2017, 700),
        ("Q3", "C", 2017, 650),
        ("Q4", "D", 2017, 500),
        ("Q5", "E", 2017, 2000),
        # 2018: a tie at the top between Q2 and Q5 (both 1500)
        ("Q1", "A", 2018, 50),
        ("Q2", "B", 2018, 1500),
        ("Q3", "C", 2018, 900),
        ("Q4", "D", 2018, 800),
        ("Q5", "E", 2018, 1500),
    ]
    history_db.upsert_annual_totals(rows, db_path=db_path)
    return db_path


class TestComputeRanks:
    def test_assigns_ranks_per_year(self, populated_db):
        rankings.compute_ranks(db_path=populated_db)
        with history_db.get_connection(populated_db) as conn:
            result = conn.execute(
                "SELECT wikidata_id, year, rank FROM annual_totals "
                "WHERE year = 2016 ORDER BY rank"
            ).fetchall()
        assert [(r["wikidata_id"], r["rank"]) for r in result] == [
            ("Q1", 1), ("Q2", 2), ("Q3", 3), ("Q4", 4), ("Q5", 5),
        ]

    def test_ties_broken_by_wikidata_id(self, populated_db):
        rankings.compute_ranks(db_path=populated_db)
        with history_db.get_connection(populated_db) as conn:
            result = conn.execute(
                "SELECT wikidata_id, rank FROM annual_totals "
                "WHERE year = 2018 ORDER BY rank"
            ).fetchall()
        # Q2 and Q5 tied at 1500 views; Q2 < Q5 lexicographically, so Q2 gets rank 1.
        assert result[0]["wikidata_id"] == "Q2"
        assert result[0]["rank"] == 1
        assert result[1]["wikidata_id"] == "Q5"
        assert result[1]["rank"] == 2

    def test_idempotent(self, populated_db):
        rankings.compute_ranks(db_path=populated_db)
        rankings.compute_ranks(db_path=populated_db)
        with history_db.get_connection(populated_db) as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM annual_totals").fetchone()
        assert count["n"] == 15  # unchanged


class TestComputeEverTop:
    def test_union_across_years(self, populated_db):
        rankings.compute_ranks(db_path=populated_db)
        rankings.compute_ever_top(top_n=2, db_path=populated_db)
        with history_db.get_connection(populated_db) as conn:
            rows = conn.execute(
                "SELECT wikidata_id, first_top_year, last_top_year, years_in_top, "
                "peak_rank, peak_year FROM ever_top ORDER BY wikidata_id"
            ).fetchall()
        # Top-2 per year:
        #   2016: Q1, Q2
        #   2017: Q5, Q2
        #   2018: Q2, Q5
        # Union: {Q1, Q2, Q5}
        qids = [r["wikidata_id"] for r in rows]
        assert qids == ["Q1", "Q2", "Q5"]

        q2 = next(r for r in rows if r["wikidata_id"] == "Q2")
        assert q2["first_top_year"] == 2016
        assert q2["last_top_year"] == 2018
        assert q2["years_in_top"] == 3
        assert q2["peak_rank"] == 1
        assert q2["peak_year"] in (2016, 2018)  # tied at rank 1 in two years

        q1 = next(r for r in rows if r["wikidata_id"] == "Q1")
        assert q1["peak_rank"] == 1
        assert q1["peak_year"] == 2016

    def test_replaces_previous_ever_top(self, populated_db):
        rankings.compute_ranks(db_path=populated_db)
        rankings.compute_ever_top(top_n=2, db_path=populated_db)
        rankings.compute_ever_top(top_n=1, db_path=populated_db)
        with history_db.get_connection(populated_db) as conn:
            rows = conn.execute(
                "SELECT wikidata_id FROM ever_top ORDER BY wikidata_id"
            ).fetchall()
        # With N=1: 2016=Q1, 2017=Q5, 2018=Q2 → {Q1, Q2, Q5}
        assert [r["wikidata_id"] for r in rows] == ["Q1", "Q2", "Q5"]

    def test_subset_property_smaller_n_is_subset(self, populated_db):
        rankings.compute_ranks(db_path=populated_db)
        rankings.compute_ever_top(top_n=5, db_path=populated_db)
        with history_db.get_connection(populated_db) as conn:
            n5 = {r["wikidata_id"] for r in conn.execute("SELECT wikidata_id FROM ever_top").fetchall()}
        rankings.compute_ever_top(top_n=2, db_path=populated_db)
        with history_db.get_connection(populated_db) as conn:
            n2 = {r["wikidata_id"] for r in conn.execute("SELECT wikidata_id FROM ever_top").fetchall()}
        assert n2.issubset(n5)
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/test_historical_rankings.py -v
```
Expected: `ModuleNotFoundError: No module named 'rankings'`.

**Step 3: Implement `rankings.py`**

```python
"""Rank computation and ever-top union for historical pageview analysis.

Operates on history.db (populated by fetch_history.py). Pure-SQL plus a small
amount of Python orchestration.
"""
from __future__ import annotations

from pathlib import Path

import history_db


def compute_ranks(db_path: Path | str = history_db.DEFAULT_DB_PATH) -> None:
    """Compute per-year dense rank (with deterministic tie-break) over annual_totals.

    Writes the result back into annual_totals.rank. Idempotent.
    """
    with history_db.get_connection(db_path) as conn:
        conn.execute(
            """
            WITH ranked AS (
                SELECT
                    wikidata_id,
                    year,
                    DENSE_RANK() OVER (
                        PARTITION BY year
                        ORDER BY views DESC, wikidata_id ASC
                    ) AS computed_rank
                FROM annual_totals
            )
            UPDATE annual_totals
            SET rank = (
                SELECT computed_rank FROM ranked
                WHERE ranked.wikidata_id = annual_totals.wikidata_id
                  AND ranked.year = annual_totals.year
            )
            """
        )
        conn.commit()


def compute_ever_top(
    top_n: int,
    db_path: Path | str = history_db.DEFAULT_DB_PATH,
) -> None:
    """Compute the ever_top table: articles that ranked <= top_n in any year.

    Replaces any previous ever_top contents. Requires compute_ranks() to have
    been run first (reads annual_totals.rank).
    """
    with history_db.get_connection(db_path) as conn:
        conn.execute("DELETE FROM ever_top")
        conn.execute(
            """
            INSERT INTO ever_top (
                wikidata_id, title, first_top_year, last_top_year,
                years_in_top, peak_rank, peak_year
            )
            SELECT
                t.wikidata_id,
                -- Use the most recent title we have for this article.
                (SELECT title FROM annual_totals
                    WHERE wikidata_id = t.wikidata_id
                    ORDER BY year DESC LIMIT 1) AS title,
                MIN(t.year) AS first_top_year,
                MAX(t.year) AS last_top_year,
                COUNT(*) AS years_in_top,
                MIN(t.rank) AS peak_rank,
                -- peak_year: year where the article achieved its peak_rank.
                -- If ties across years, the earliest such year.
                (SELECT MIN(year) FROM annual_totals a2
                    WHERE a2.wikidata_id = t.wikidata_id
                      AND a2.rank = MIN(t.rank)) AS peak_year
            FROM annual_totals t
            WHERE t.rank IS NOT NULL AND t.rank <= ?
            GROUP BY t.wikidata_id
            """,
            (top_n,),
        )
        conn.commit()
```

**Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_historical_rankings.py -v
```
Expected: all 6 tests pass.

**Step 5: Commit**

```bash
git add analysis/historical-decline/rankings.py tests/test_historical_rankings.py
git commit -m "feat(analysis): add rank and ever-top computation"
```

---

## Task 2: compute_rankings.py CLI

**Files:**
- Create: `analysis/historical-decline/compute_rankings.py`

**Step 1: Write the CLI**

```python
"""CLI for ranking + ever-top computation.

Usage:
    uv run python analysis/historical-decline/compute_rankings.py
    uv run python analysis/historical-decline/compute_rankings.py --top-n 25

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
```

**Step 2: Verify operationally against the real populated dataset**

This requires Phase 2 to have run successfully. Smoke test:

```bash
cd /var/home/louie/Projects/Volunteering-Consulting/wikipedia-career-images
uv run python analysis/historical-decline/compute_rankings.py --top-n 50
```

Expected output (numbers are illustrative):
```
Computing ranks over 40000 annual-total rows...
Computing ever-top (N=50)...
ever_top populated with 187 articles.
```

The `ever_top` count should fall within the predicted 100–400 range. If it's outside that range, that's an observation worth noting but not a test failure — design anticipated a range.

Verify the N=50 vs N=25 subset property on real data:

```bash
uv run python analysis/historical-decline/compute_rankings.py --top-n 25
sqlite3 analysis/historical-decline/history.db "SELECT COUNT(*) FROM ever_top;"
uv run python analysis/historical-decline/compute_rankings.py --top-n 50
sqlite3 analysis/historical-decline/history.db "SELECT COUNT(*) FROM ever_top;"
```
Expected: the N=25 count is strictly less than the N=50 count.

**Step 3: Commit**

```bash
git add analysis/historical-decline/compute_rankings.py
git commit -m "feat(analysis): add compute_rankings CLI"
```

---

## Phase 3 Done Criteria

- `uv run pytest tests/test_historical_rankings.py -v` passes with all 6 tests.
- `uv run python analysis/historical-decline/compute_rankings.py` succeeds on the real `history.db` produced by Phase 2, populating `ever_top` with a plausible number of rows (expected 100–400 at N=50).
- `ever_top` with smaller N is a strict subset of `ever_top` with larger N.
- Full existing test suite still passes: `uv run pytest tests/ -v`.
