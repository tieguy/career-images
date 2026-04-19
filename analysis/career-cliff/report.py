"""First-pass decline report for the historical pageview analysis subproject.

Reads ever_top + annual_totals from history.db, computes baseline (2016-2019)
vs recent (2022-2025) totals per article, and emits:
- A stdout summary (article count, median/p25/p75 percent change, top-10 fallen giants)
- A CSV at analysis/career-cliff/output/decline_summary.csv

Usage:
    uv run python analysis/career-cliff/report.py
    uv run python analysis/career-cliff/report.py --output custom/path.csv
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import history_db  # noqa: E402

# Baseline: 2016-01 through 2019-12 (48 months, pre-pandemic and pre-LLM).
# Recent:   2025-04 through 2026-03 (12 months / 4 quarters, peak LLM era).
# Chosen as a single rolling year so every month is counted once per window,
# eliminating residual seasonal bias. The 2020-2024 interregnum is intentionally
# excluded: COVID traffic distortions plus a slow-onset LLM effect would muddy
# a direct before/after contrast.
BASELINE_START = (2016, 1)
BASELINE_END = (2019, 12)
RECENT_START = (2025, 4)
RECENT_END = (2026, 3)

DEFAULT_CSV = Path(__file__).parent / "output" / "decline_summary.csv"


def _ym_key(year: int, month: int) -> int:
    """Encode (year, month) as a sortable int: 201601 etc."""
    return year * 100 + month


def _window_size(start: tuple[int, int], end: tuple[int, int]) -> int:
    """Count months inclusive from (start_year, start_month) to (end_year, end_month)."""
    return (end[0] - start[0]) * 12 + (end[1] - start[1]) + 1


BASELINE_MONTHS_EXPECTED = _window_size(BASELINE_START, BASELINE_END)
RECENT_MONTHS_EXPECTED = _window_size(RECENT_START, RECENT_END)


def compute_decline_rows(
    db_path: Path | str = history_db.DEFAULT_DB_PATH,
) -> list[dict]:
    """Per-ever-top-article: baseline vs recent per-month averages and % change.

    Only includes articles with full coverage in BOTH windows. Growth/decline
    is computed on per-month averages so unequal window sizes compare fairly
    (baseline = 48 months, recent = 15 months).
    """
    baseline_lo = _ym_key(*BASELINE_START)
    baseline_hi = _ym_key(*BASELINE_END)
    recent_lo = _ym_key(*RECENT_START)
    recent_hi = _ym_key(*RECENT_END)

    query = """
        SELECT
            et.wikidata_id,
            et.title,
            et.peak_rank,
            et.peak_year,
            COUNT(CASE WHEN (mv.year*100+mv.month) BETWEEN ? AND ?
                       THEN 1 END) AS baseline_months,
            COUNT(CASE WHEN (mv.year*100+mv.month) BETWEEN ? AND ?
                       THEN 1 END) AS recent_months,
            COALESCE(SUM(CASE WHEN (mv.year*100+mv.month) BETWEEN ? AND ?
                              THEN mv.views END), 0) AS baseline_sum,
            COALESCE(SUM(CASE WHEN (mv.year*100+mv.month) BETWEEN ? AND ?
                              THEN mv.views END), 0) AS recent_sum
        FROM ever_top et
        LEFT JOIN monthly_views mv ON mv.wikidata_id = et.wikidata_id
        GROUP BY et.wikidata_id, et.title, et.peak_rank, et.peak_year
        ORDER BY et.wikidata_id
    """
    params = [
        baseline_lo, baseline_hi,
        recent_lo, recent_hi,
        baseline_lo, baseline_hi,
        recent_lo, recent_hi,
    ]
    with history_db.get_connection(db_path) as conn:
        raw = conn.execute(query, params).fetchall()

    rows: list[dict] = []
    skipped_partial = 0
    for r in raw:
        if (
            r["baseline_months"] != BASELINE_MONTHS_EXPECTED
            or r["recent_months"] != RECENT_MONTHS_EXPECTED
        ):
            skipped_partial += 1
            continue
        baseline_sum = r["baseline_sum"]
        recent_sum = r["recent_sum"]
        if baseline_sum == 0:
            skipped_partial += 1
            continue
        baseline_per_month = baseline_sum / BASELINE_MONTHS_EXPECTED
        recent_per_month = recent_sum / RECENT_MONTHS_EXPECTED
        pct = (recent_per_month - baseline_per_month) * 100.0 / baseline_per_month
        rows.append({
            "wikidata_id": r["wikidata_id"],
            "title": r["title"],
            "baseline_total": baseline_sum,
            "recent_total": recent_sum,
            "baseline_per_month": baseline_per_month,
            "recent_per_month": recent_per_month,
            "pct_change": pct,
            "peak_rank": r["peak_rank"],
            "peak_year": r["peak_year"],
        })

    if skipped_partial:
        print(
            f"Note: skipped {skipped_partial} ever-top articles missing full "
            f"coverage in both windows (baseline={BASELINE_MONTHS_EXPECTED} months, "
            f"recent={RECENT_MONTHS_EXPECTED} months).",
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
        (mv_count,) = conn.execute("SELECT COUNT(*) FROM monthly_views").fetchone()
        (et_count,) = conn.execute("SELECT COUNT(*) FROM ever_top").fetchone()
        (log_count,) = conn.execute("SELECT COUNT(*) FROM fetch_log").fetchone()
        (latest_fetch,) = conn.execute(
            "SELECT MAX(fetched_at) FROM fetch_log"
        ).fetchone()
    print()
    print("Provenance:")
    print(f"  annual_totals rows: {at_count:,}")
    print(f"  monthly_views rows: {mv_count:,}")
    print(f"  ever_top rows:      {et_count:,}")
    print(f"  fetch_log rows:     {log_count:,}")
    print(f"  latest fetch_at:    {latest_fetch or '(none)'}")


def print_summary(rows: list[dict], summary: dict) -> None:
    print()
    print("=" * 70)
    print("Historical Pageview Decline — Career Articles")
    print("=" * 70)
    print(
        f"Baseline window: {BASELINE_START[0]}-{BASELINE_START[1]:02d} through "
        f"{BASELINE_END[0]}-{BASELINE_END[1]:02d} ({BASELINE_MONTHS_EXPECTED} months)"
    )
    print(
        f"Recent window:   {RECENT_START[0]}-{RECENT_START[1]:02d} through "
        f"{RECENT_END[0]}-{RECENT_END[1]:02d} ({RECENT_MONTHS_EXPECTED} months)"
    )
    print(f"Full-coverage articles analyzed: {summary['n']} (per-month normalized)")
    if summary["n"] == 0:
        print("No full-coverage ever-top articles to analyze.")
        return
    print(f"Median percent change: {summary['median_pct_change']:+.1f}%")
    print(f"  P25: {summary['p25_pct_change']:+.1f}%")
    print(f"  P75: {summary['p75_pct_change']:+.1f}%")
    print()
    print("Top 10 'fallen giants' (biggest per-month average drop):")
    print("-" * 70)
    fallen = sorted(
        [r for r in rows if r["recent_per_month"] < r["baseline_per_month"]],
        key=lambda r: (r["recent_per_month"] - r["baseline_per_month"]),
    )[:10]
    if fallen:
        for r in fallen:
            print(
                f"  {r['title']:<40} "
                f"{r['baseline_per_month']:>10,.0f}/mo -> {r['recent_per_month']:>10,.0f}/mo "
                f"({r['pct_change']:+6.1f}%)"
            )
    else:
        print("  (no articles showed decline)")
    print()


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "wikidata_id", "title",
        "baseline_total", "recent_total",
        "baseline_per_month", "recent_per_month",
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
