"""Smoke tests for report.py — end-to-end on a tiny fixture dataset."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ANALYSIS_DIR = Path(__file__).parent.parent / "analysis" / "historical-decline"
sys.path.insert(0, str(ANALYSIS_DIR))

import history_db
import rankings
import report


@pytest.fixture
def fixture_db(tmp_path):
    """Build a small history.db with predictable decline characteristics."""
    db_path = tmp_path / "history.db"
    history_db.init_schema(db_path)
    rows = []
    # 3 articles, 10 years each (2016-2025).
    # Q1: steady decline — baseline avg 1000, recent avg 250 (-75%)
    # Q2: steady — baseline avg 500, recent avg 500 (no change)
    # Q3: growing — baseline avg 100, recent avg 400 (+300%)
    for year in range(2016, 2026):
        if year <= 2019:
            q1_views, q2_views, q3_views = 1000, 500, 100
        elif year in (2020, 2021):
            q1_views, q2_views, q3_views = 600, 500, 200  # COVID window (excluded)
        else:  # 2022-2025
            q1_views, q2_views, q3_views = 250, 500, 400
        rows.extend([
            ("Q1", "Article One", year, q1_views),
            ("Q2", "Article Two", year, q2_views),
            ("Q3", "Article Three", year, q3_views),
        ])
    history_db.upsert_annual_totals(rows, db_path=db_path)
    rankings.compute_ranks(db_path=db_path)
    rankings.compute_ever_top(top_n=3, db_path=db_path)
    return db_path


class TestComputeDeclineRows:
    def test_returns_row_per_ever_top_article(self, fixture_db):
        rows = report.compute_decline_rows(db_path=fixture_db)
        assert len(rows) == 3
        qids = {r["wikidata_id"] for r in rows}
        assert qids == {"Q1", "Q2", "Q3"}

    def test_baseline_and_recent_totals(self, fixture_db):
        rows = report.compute_decline_rows(db_path=fixture_db)
        by_qid = {r["wikidata_id"]: r for r in rows}
        # Q1: baseline = 4×1000 = 4000; recent = 4×250 = 1000
        assert by_qid["Q1"]["baseline_total"] == 4000
        assert by_qid["Q1"]["recent_total"] == 1000
        # Q3: baseline = 4×100 = 400; recent = 4×400 = 1600
        assert by_qid["Q3"]["baseline_total"] == 400
        assert by_qid["Q3"]["recent_total"] == 1600

    def test_pct_change(self, fixture_db):
        rows = report.compute_decline_rows(db_path=fixture_db)
        by_qid = {r["wikidata_id"]: r for r in rows}
        assert by_qid["Q1"]["pct_change"] == pytest.approx(-75.0, abs=0.01)
        assert by_qid["Q2"]["pct_change"] == pytest.approx(0.0, abs=0.01)
        assert by_qid["Q3"]["pct_change"] == pytest.approx(300.0, abs=0.01)


class TestSummarize:
    def test_median_and_quartiles(self, fixture_db):
        rows = report.compute_decline_rows(db_path=fixture_db)
        summary = report.summarize(rows)
        assert summary["n"] == 3
        assert summary["median_pct_change"] == pytest.approx(0.0, abs=0.01)
        # With only 3 values, p25/p75 straddle -75 and 300; just sanity-check order.
        assert summary["p25_pct_change"] < summary["p75_pct_change"]


class TestCsvExport:
    def test_writes_csv_with_expected_columns(self, fixture_db, tmp_path):
        out_path = tmp_path / "out.csv"
        rows = report.compute_decline_rows(db_path=fixture_db)
        report.write_csv(rows, out_path)
        assert out_path.exists()
        with open(out_path) as f:
            reader = csv.DictReader(f)
            columns = reader.fieldnames
            data_rows = list(reader)
        assert set(columns) == {
            "wikidata_id", "title", "baseline_total", "recent_total", "pct_change",
            "peak_rank", "peak_year",
        }
        assert len(data_rows) == 3


class TestFullReport:
    def test_runs_end_to_end(self, fixture_db, tmp_path, capsys):
        out_csv = tmp_path / "decline.csv"
        report.run(db_path=fixture_db, output_csv=out_csv)
        captured = capsys.readouterr()
        assert "Ever-top articles analyzed: 3" in captured.out
        assert "Median percent change" in captured.out
        assert out_csv.exists()

    def test_errors_cleanly_on_empty_db(self, tmp_path, capsys):
        empty_db = tmp_path / "empty.db"
        history_db.init_schema(empty_db)
        rc = report.run(db_path=empty_db, output_csv=tmp_path / "out.csv")
        captured = capsys.readouterr()
        assert rc != 0
        assert "ever_top is empty" in captured.err
