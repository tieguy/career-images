# Historical Pageview Analysis — Phase 4: First-Pass Report

> **For Claude:** REQUIRED SUB-SKILL: Use ed3d-plan-and-execute:executing-an-implementation-plan to implement this plan task-by-task.

**Goal:** Implement `report.py` — a minimal, text-output CLI that reads `ever_top` and `annual_totals` from `history.db` and answers the primary question: have pageviews declined for career articles between 2016–2019 and 2022–2025, and which articles fell the furthest? Emits a summary to stdout plus a CSV export for ad-hoc follow-up.

**Architecture:** Pure-SQL reads from `history.db`. Computes per-article baseline-vs-recent totals, prints a top-level summary (article count, median percent change, p25/p75), prints a short "fallen giants" table, writes a full CSV. No charts in this phase — the user has explicitly deferred visualization.

**Tech Stack:** Python 3.13, sqlite3, csv (stdlib only). No new dependencies.

**Scope:** Phase 4 of 4. The user noted the report is intentionally a first pass — details will be iterated on after seeing real output, so this phase commits only to a minimally coherent artifact.

**Codebase verified:** 2026-04-18. SQLite supports `PERCENTILE_CONT` only via extensions; we compute percentiles in Python using `statistics.quantiles()` (stdlib, Python 3.13). No new dependencies required. `ever_top` and `annual_totals` schema confirmed from Phase 1.

**Window choice rationale:** The design specifies baseline 2016–2019 (pre-LLM, pre-pandemic) and recent 2022–2025 (post-ChatGPT). 2020–2021 is excluded from both windows to avoid COVID-era distortion. This is a defensible default; if the user wants to adjust, it's a CLI flag.

---

## Task 1: Report computation core

**Files:**
- Create: `analysis/historical-decline/report.py`
- Create: `tests/test_historical_report.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/test_historical_report.py -v
```
Expected: `ModuleNotFoundError: No module named 'report'`.

**Step 3: Implement `report.py`**

```python
"""First-pass decline report for the historical pageview analysis subproject.

Reads ever_top + annual_totals from history.db, computes baseline (2016-2019)
vs recent (2022-2025) totals per article, and emits:
- A stdout summary (article count, median/p25/p75 percent change, top-10 fallen giants)
- A CSV at analysis/historical-decline/output/decline_summary.csv

Usage:
    uv run python analysis/historical-decline/report.py
    uv run python analysis/historical-decline/report.py --output custom/path.csv
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import history_db

BASELINE_YEARS = (2016, 2017, 2018, 2019)
RECENT_YEARS = (2022, 2023, 2024, 2025)
DEFAULT_CSV = Path(__file__).parent / "output" / "decline_summary.csv"


def compute_decline_rows(
    db_path: Path | str = history_db.DEFAULT_DB_PATH,
) -> list[dict]:
    """Per-ever-top-article: baseline total, recent total, pct change.

    Returns a list of dicts; articles with zero baseline are skipped (division
    by zero would make pct_change undefined — flag in stderr when it happens).
    """
    baseline_placeholders = ",".join("?" for _ in BASELINE_YEARS)
    recent_placeholders = ",".join("?" for _ in RECENT_YEARS)
    query = f"""
        SELECT
            et.wikidata_id,
            et.title,
            et.peak_rank,
            et.peak_year,
            COALESCE(SUM(CASE WHEN at.year IN ({baseline_placeholders})
                              THEN at.views END), 0) AS baseline_total,
            COALESCE(SUM(CASE WHEN at.year IN ({recent_placeholders})
                              THEN at.views END), 0) AS recent_total
        FROM ever_top et
        LEFT JOIN annual_totals at ON at.wikidata_id = et.wikidata_id
        GROUP BY et.wikidata_id, et.title, et.peak_rank, et.peak_year
        ORDER BY et.wikidata_id
    """
    params = list(BASELINE_YEARS) + list(RECENT_YEARS)
    with history_db.get_connection(db_path) as conn:
        raw = conn.execute(query, params).fetchall()

    rows: list[dict] = []
    skipped_zero_baseline = 0
    for r in raw:
        baseline = r["baseline_total"]
        recent = r["recent_total"]
        if baseline == 0:
            skipped_zero_baseline += 1
            continue
        pct = (recent - baseline) * 100.0 / baseline
        rows.append({
            "wikidata_id": r["wikidata_id"],
            "title": r["title"],
            "baseline_total": baseline,
            "recent_total": recent,
            "pct_change": pct,
            "peak_rank": r["peak_rank"],
            "peak_year": r["peak_year"],
        })

    if skipped_zero_baseline:
        print(
            f"Note: skipped {skipped_zero_baseline} articles with zero baseline views.",
            file=sys.stderr,
        )
    return rows


def summarize(rows: list[dict]) -> dict:
    """Compute median, p25, p75 of pct_change across rows."""
    if not rows:
        return {"n": 0, "median_pct_change": None, "p25_pct_change": None, "p75_pct_change": None}
    pct_changes = sorted(r["pct_change"] for r in rows)
    median = statistics.median(pct_changes)
    if len(pct_changes) >= 4:
        q1, _, q3 = statistics.quantiles(pct_changes, n=4)
    else:
        # Fallback for tiny datasets (tests use 3 rows).
        q1 = pct_changes[0]
        q3 = pct_changes[-1]
    return {
        "n": len(rows),
        "median_pct_change": median,
        "p25_pct_change": q1,
        "p75_pct_change": q3,
    }


def print_provenance(db_path: Path | str) -> None:
    """Print reproducibility info: row counts + latest fetch_log timestamp."""
    with history_db.get_connection(db_path) as conn:
        (at_count,) = conn.execute("SELECT COUNT(*) FROM annual_totals").fetchone()
        (et_count,) = conn.execute("SELECT COUNT(*) FROM ever_top").fetchone()
        (log_count,) = conn.execute("SELECT COUNT(*) FROM fetch_log").fetchone()
        (latest_fetch,) = conn.execute(
            "SELECT MAX(fetched_at) FROM fetch_log"
        ).fetchone()
    print()
    print("Provenance:")
    print(f"  annual_totals rows: {at_count:,}")
    print(f"  ever_top rows:      {et_count:,}")
    print(f"  fetch_log rows:     {log_count:,}")
    print(f"  latest fetch_at:    {latest_fetch or '(none)'}")


def print_summary(rows: list[dict], summary: dict) -> None:
    print()
    print("=" * 60)
    print("Historical Pageview Decline — Career Articles")
    print("=" * 60)
    print(f"Baseline window: {BASELINE_YEARS[0]}-{BASELINE_YEARS[-1]}")
    print(f"Recent window:   {RECENT_YEARS[0]}-{RECENT_YEARS[-1]}")
    print(f"Ever-top articles analyzed: {summary['n']}")
    if summary["n"] == 0:
        print("No articles with non-zero baseline to analyze.")
        return
    print(f"Median percent change: {summary['median_pct_change']:+.1f}%")
    print(f"  P25: {summary['p25_pct_change']:+.1f}%")
    print(f"  P75: {summary['p75_pct_change']:+.1f}%")
    print()
    print("Top 10 'fallen giants' (biggest absolute view drop):")
    print("-" * 60)
    fallen = sorted(
        rows,
        key=lambda r: (r["recent_total"] - r["baseline_total"]),
    )[:10]
    for r in fallen:
        drop = r["baseline_total"] - r["recent_total"]
        print(
            f"  {r['title']:<40} "
            f"{r['baseline_total']:>10,} -> {r['recent_total']:>10,} "
            f"({r['pct_change']:+6.1f}%, -{drop:,})"
        )
    print()


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "wikidata_id", "title", "baseline_total", "recent_total",
        "pct_change", "peak_rank", "peak_year",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r[k] for k in columns})


def run(
    db_path: Path | str = history_db.DEFAULT_DB_PATH,
    output_csv: Path = DEFAULT_CSV,
) -> int:
    with history_db.get_connection(db_path) as conn:
        (ever_top_count,) = conn.execute("SELECT COUNT(*) FROM ever_top").fetchone()
    if ever_top_count == 0:
        print(
            "ever_top is empty. Run compute_rankings.py first.",
            file=sys.stderr,
        )
        return 1

    rows = compute_decline_rows(db_path=db_path)
    summary = summarize(rows)
    print_summary(rows, summary)
    print_provenance(db_path)
    write_csv(rows, Path(output_csv))
    print(f"CSV written to: {output_csv}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=history_db.DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args()
    return run(db_path=args.db, output_csv=args.output)


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_historical_report.py -v
```
Expected: all 7 test methods pass.

**Step 5: Smoke test against real data**

This requires Phase 2 and Phase 3 to have run successfully.

```bash
cd /var/home/louie/Projects/Volunteering-Consulting/wikipedia-career-images
uv run python analysis/historical-decline/report.py
```

Expected output shape (actual numbers will vary):
```
============================================================
Historical Pageview Decline — Career Articles
============================================================
Baseline window: 2016-2019
Recent window:   2022-2025
Ever-top articles analyzed: 187
Median percent change: -28.3%
  P25: -52.1%
  P75: -4.7%

Top 10 'fallen giants' (biggest absolute view drop):
------------------------------------------------------------
  Software_engineer                         5,432,100 ->  1,234,567 ( -77.3%, -4,197,533)
  ...

CSV written to: .../analysis/historical-decline/output/decline_summary.csv
```

The actual median is the interesting number — it answers the user's question. The CSV enables ad-hoc follow-up analysis.

Inspect the CSV:

```bash
head -5 analysis/historical-decline/output/decline_summary.csv
wc -l analysis/historical-decline/output/decline_summary.csv
```
Expected: header plus one row per ever-top article.

**Step 6: Commit**

```bash
git add analysis/historical-decline/report.py tests/test_historical_report.py
git commit -m "feat(analysis): add first-pass decline report"
```

---

## Phase 4 Done Criteria

- `uv run pytest tests/test_historical_report.py -v` passes with all 7 tests.
- `uv run python analysis/historical-decline/report.py` runs successfully on the real pipeline output, producing:
  - A stdout summary with article count, median/p25/p75 percent change, and top-10 fallen giants.
  - A CSV at `analysis/historical-decline/output/decline_summary.csv` with one row per ever-top article.
- The full existing test suite still passes: `uv run pytest tests/ -v`.
- The report's output is enough to answer the user's original question: "were the top career articles much more visited in earlier years?" — median percent change and the fallen-giants table together give a defensible yes-or-no answer.

---

## Post-Phase Follow-Ups (Not In Scope)

These are explicitly deferred per the user's direction and the design's "Future extensions" section. Do NOT implement as part of Phase 4:

- Per-category cuts (joining `ever_top` against `careers.category`).
- Charts / visualizations (matplotlib, plotly).
- Automatic redirect resolution for articles flagged `missing` in `fetch_log`.
- Pre-2016 data via `pagecounts-ez` (would require flagging the methodology break in output).
- Mobile-vs-desktop split.
- Monperrus-style per-article time-series plots.
- Attribution analysis against the ChatGPT launch date (2022-11-30).

After reviewing the first-pass report, the user will decide which (if any) of these to pursue in a follow-up plan.
