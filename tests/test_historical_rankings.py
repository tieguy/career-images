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
