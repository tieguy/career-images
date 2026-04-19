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

import history_db  # noqa: E402

BASELINE_YEARS = (2016, 2017, 2018, 2019)
RECENT_YEARS = (2022, 2023, 2024, 2025)
REQUIRED_COVERAGE_YEARS = 10  # articles must have data for all of 2016–2025
DEFAULT_CSV = Path(__file__).parent / "output" / "decline_summary.csv"


def compute_decline_rows(
    db_path: Path | str = history_db.DEFAULT_DB_PATH,
) -> list[dict]:
    """Per-ever-top-article: baseline total, recent total, pct change.

    Only includes articles with full 10-year coverage (2016–2025). Articles with
    partial coverage — e.g. Wikipedia pages that didn't exist in 2016, or that
    had their titles drift — are skipped so the baseline-vs-recent comparison is
    strictly apples-to-apples.
    """
    baseline_placeholders = ",".join("?" for _ in BASELINE_YEARS)
    recent_placeholders = ",".join("?" for _ in RECENT_YEARS)
    query = f"""
        SELECT
            et.wikidata_id,
            et.title,
            et.peak_rank,
            et.peak_year,
            COUNT(DISTINCT at.year) AS years_covered,
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
    skipped_partial = 0
    for r in raw:
        if r["years_covered"] != REQUIRED_COVERAGE_YEARS:
            skipped_partial += 1
            continue
        baseline = r["baseline_total"]
        recent = r["recent_total"]
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

    if skipped_partial:
        print(
            f"Note: skipped {skipped_partial} ever-top articles without full "
            f"{REQUIRED_COVERAGE_YEARS}-year coverage (partial data or title drift).",
            file=sys.stderr,
        )
    return rows


def summarize(rows: list[dict]) -> dict:
    """Compute median, p25, p75 of pct_change across rows."""
    if not rows:
        return {"n": 0, "median_pct_change": None, "p25_pct_change": None, "p75_pct_change": None}
    pct_changes = sorted(r["pct_change"] for r in rows)
    median = statistics.median(pct_changes)
    q1, _, q3 = statistics.quantiles(pct_changes, n=4, method="inclusive")
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
    print(f"Full-coverage articles analyzed: {summary['n']} (apples-to-apples, 2016–2025)")
    if summary["n"] == 0:
        print("No full-coverage ever-top articles to analyze.")
        return
    print(f"Median percent change: {summary['median_pct_change']:+.1f}%")
    print(f"  P25: {summary['p25_pct_change']:+.1f}%")
    print(f"  P75: {summary['p75_pct_change']:+.1f}%")
    print()
    print("Top 10 'fallen giants' (biggest absolute view drop):")
    print("-" * 60)
    fallen = sorted(
        [r for r in rows if r["recent_total"] < r["baseline_total"]],
        key=lambda r: (r["recent_total"] - r["baseline_total"]),
    )[:10]
    if fallen:
        for r in fallen:
            drop = r["baseline_total"] - r["recent_total"]
            print(
                f"  {r['title']:<40} "
                f"{r['baseline_total']:>10,} -> {r['recent_total']:>10,} "
                f"({r['pct_change']:+6.1f}%, -{drop:,})"
            )
    else:
        print("  (no articles showed decline)")
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
