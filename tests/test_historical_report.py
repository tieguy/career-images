"""Smoke tests for report.py — end-to-end on a tiny fixture dataset."""
from __future__ import annotations

import csv

import pytest

import history_db
import rankings
import report


def _months_in_range(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """Enumerate (year, month) pairs inclusive from start to end."""
    y, m = start
    out: list[tuple[int, int]] = []
    while (y, m) <= end:
        out.append((y, m))
        m += 1
        if m > 12:
            y += 1
            m = 1
    return out


@pytest.fixture
def fixture_db(tmp_path):
    """Build a small history.db with predictable decline characteristics.

    3 articles with monthly data covering both the baseline (2016-01..2019-12)
    and recent (2025-01..2026-03) windows plus enough years to qualify for
    ever_top ranking.
    """
    db_path = tmp_path / "history.db"
    history_db.init_schema(db_path)

    # Per-month views, chosen so that:
    # Q1: baseline 1000/mo → recent 250/mo = -75%
    # Q2: baseline 500/mo  → recent 500/mo =   0%
    # Q3: baseline 100/mo  → recent 400/mo = +300%
    per_article_per_month = {
        "Q1": {"baseline": 1000, "interregnum": 600, "recent": 250},
        "Q2": {"baseline": 500,  "interregnum": 500, "recent": 500},
        "Q3": {"baseline": 100,  "interregnum": 200, "recent": 400},
    }
    titles = {"Q1": "Article One", "Q2": "Article Two", "Q3": "Article Three"}

    monthly_rows = []
    annual_totals_by_article: dict[str, dict[int, int]] = {
        qid: {} for qid in per_article_per_month
    }

    baseline_months = set(_months_in_range((2016, 1), (2019, 12)))
    recent_months = set(_months_in_range((2025, 1), (2026, 3)))
    all_months = _months_in_range((2016, 1), (2026, 3))

    for qid, profile in per_article_per_month.items():
        for (year, month) in all_months:
            if (year, month) in baseline_months:
                v = profile["baseline"]
            elif (year, month) in recent_months:
                v = profile["recent"]
            else:
                v = profile["interregnum"]
            monthly_rows.append((qid, titles[qid], year, month, v))
            annual_totals_by_article[qid][year] = annual_totals_by_article[qid].get(year, 0) + v

    history_db.upsert_monthly_views(monthly_rows, db_path=db_path)

    # Also populate annual_totals for complete years only (matches fetcher semantics).
    annual_rows = []
    for qid, by_year in annual_totals_by_article.items():
        for year, views in by_year.items():
            # 2026 is incomplete (only Jan-Mar), skip.
            if year == 2026:
                continue
            annual_rows.append((qid, titles[qid], year, views))
    history_db.upsert_annual_totals(annual_rows, db_path=db_path)

    rankings.compute_ranks(db_path=db_path)
    rankings.compute_ever_top(top_n=3, db_path=db_path)
    return db_path


class TestComputeDeclineRows:
    def test_returns_row_per_ever_top_article(self, fixture_db):
        rows = report.compute_decline_rows(db_path=fixture_db)
        assert len(rows) == 3
        qids = {r["wikidata_id"] for r in rows}
        assert qids == {"Q1", "Q2", "Q3"}

    def test_per_month_averages(self, fixture_db):
        rows = report.compute_decline_rows(db_path=fixture_db)
        by_qid = {r["wikidata_id"]: r for r in rows}
        assert by_qid["Q1"]["baseline_per_month"] == pytest.approx(1000.0)
        assert by_qid["Q1"]["recent_per_month"] == pytest.approx(250.0)
        assert by_qid["Q3"]["baseline_per_month"] == pytest.approx(100.0)
        assert by_qid["Q3"]["recent_per_month"] == pytest.approx(400.0)

    def test_pct_change(self, fixture_db):
        rows = report.compute_decline_rows(db_path=fixture_db)
        by_qid = {r["wikidata_id"]: r for r in rows}
        assert by_qid["Q1"]["pct_change"] == pytest.approx(-75.0, abs=0.01)
        assert by_qid["Q2"]["pct_change"] == pytest.approx(0.0, abs=0.01)
        assert by_qid["Q3"]["pct_change"] == pytest.approx(300.0, abs=0.01)


class TestPartialCoverageFilter:
    def test_excludes_articles_missing_window_months(self, tmp_path, capsys):
        """Articles without full coverage of both windows are excluded."""
        db_path = tmp_path / "history.db"
        history_db.init_schema(db_path)

        full_months = _months_in_range((2016, 1), (2026, 3))
        partial_months = _months_in_range((2020, 1), (2026, 3))

        monthly_rows = []
        for (y, m) in full_months:
            monthly_rows.append(("Qfull", "Full Coverage", y, m, 1000))
        for (y, m) in partial_months:
            monthly_rows.append(("Qpartial", "Partial Coverage", y, m, 999))
        history_db.upsert_monthly_views(monthly_rows, db_path=db_path)

        # Also populate annual_totals for ranking eligibility.
        annual_rows = []
        for y in range(2016, 2026):
            annual_rows.append(("Qfull", "Full Coverage", y, 12 * 1000))
        for y in range(2020, 2026):
            annual_rows.append(("Qpartial", "Partial Coverage", y, 12 * 999))
        history_db.upsert_annual_totals(annual_rows, db_path=db_path)

        rankings.compute_ranks(db_path=db_path)
        rankings.compute_ever_top(top_n=5, db_path=db_path)

        decline_rows = report.compute_decline_rows(db_path=db_path)
        captured = capsys.readouterr()

        qids = {r["wikidata_id"] for r in decline_rows}
        assert "Qfull" in qids
        assert "Qpartial" not in qids
        assert "missing full coverage in both windows" in captured.err


class TestSummarize:
    def test_median_and_quartiles(self, fixture_db):
        rows = report.compute_decline_rows(db_path=fixture_db)
        summary = report.summarize(rows)
        assert summary["n"] == 3
        assert summary["median_pct_change"] == pytest.approx(0.0, abs=0.01)
        # inclusive quantiles on [-75, 0, 300]: p25=-37.5, p75=150.0
        assert summary["p25_pct_change"] == pytest.approx(-37.5, abs=0.01)
        assert summary["p75_pct_change"] == pytest.approx(150.0, abs=0.01)


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
            "wikidata_id", "title",
            "baseline_total", "recent_total",
            "baseline_per_month", "recent_per_month",
            "pct_change", "peak_rank", "peak_year",
        }
        assert len(data_rows) == 3


class TestFullReport:
    def test_runs_end_to_end(self, fixture_db, tmp_path, capsys):
        out_csv = tmp_path / "decline.csv"
        report.run(db_path=fixture_db, output_csv=out_csv)
        captured = capsys.readouterr()
        assert "Full-coverage articles analyzed: 3" in captured.out
        assert "Median percent change" in captured.out
        assert "Provenance:" in captured.out
        assert "monthly_views rows:" in captured.out
        assert "latest fetch_at:" in captured.out
        assert out_csv.exists()

    def test_fallen_giants_excludes_growers_and_has_no_double_minus(
        self, fixture_db, tmp_path, capsys
    ):
        """Verify fallen giants filters to decliners only, with no --N display bug."""
        out_csv = tmp_path / "decline.csv"
        report.run(db_path=fixture_db, output_csv=out_csv)
        captured = capsys.readouterr()

        fallen_section = captured.out.split("Top 10 'fallen giants'")[1].split("Provenance:")[0]
        data_lines = [
            line for line in fallen_section.split("\n")
            if line.strip() and not line.startswith("-")
        ]
        data_section = "\n".join(data_lines)
        assert "--" not in data_section
        assert "Article One" in fallen_section
        assert "Article Three" not in fallen_section

    def test_errors_cleanly_on_empty_db(self, tmp_path, capsys):
        empty_db = tmp_path / "empty.db"
        history_db.init_schema(empty_db)
        rc = report.run(db_path=empty_db, output_csv=tmp_path / "out.csv")
        captured = capsys.readouterr()
        assert rc != 0
        assert "ever_top is empty" in captured.err
