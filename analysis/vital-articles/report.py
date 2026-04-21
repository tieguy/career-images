"""Per-bucket pageview decline report for the Vital Articles subproject.

Reads samples + monthly_views from vital.db, computes 2016-19 baseline vs
2025-04..2026-03 recent per-month averages for each sampled article (with
full coverage in both windows), and aggregates by primary_topic bucket.

Windows match career-cliff exactly so the two analyses are comparable:
- Baseline: 2016-01 .. 2019-12 (48 months, pre-pandemic / pre-LLM)
- Recent:   2025-04 .. 2026-03 (12-month rolling year, peak-LLM era)

Usage:
    uv run python analysis/vital-articles/report.py
    uv run python analysis/vital-articles/report.py --output custom/path.csv
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import vital_db  # noqa: E402

BASELINE_START = (2016, 1)
BASELINE_END = (2019, 12)
RECENT_START = (2025, 4)
RECENT_END = (2026, 3)

DEFAULT_CSV = Path(__file__).parent / "output" / "decline_by_bucket.csv"


def _ym_key(year: int, month: int) -> int:
    return year * 100 + month


def _window_size(start: tuple[int, int], end: tuple[int, int]) -> int:
    return (end[0] - start[0]) * 12 + (end[1] - start[1]) + 1


BASELINE_MONTHS_EXPECTED = _window_size(BASELINE_START, BASELINE_END)
RECENT_MONTHS_EXPECTED = _window_size(RECENT_START, RECENT_END)


def compute_decline_rows(
    db_path: Path | str = vital_db.DEFAULT_DB_PATH,
) -> tuple[list[dict], int]:
    """Per-sampled-article decline stats. Returns (rows, skipped_partial)."""
    baseline_lo = _ym_key(*BASELINE_START)
    baseline_hi = _ym_key(*BASELINE_END)
    recent_lo = _ym_key(*RECENT_START)
    recent_hi = _ym_key(*RECENT_END)

    query = """
        SELECT
            s.title,
            s.primary_topic,
            COUNT(CASE WHEN (mv.year*100+mv.month) BETWEEN ? AND ?
                       THEN 1 END) AS baseline_months,
            COUNT(CASE WHEN (mv.year*100+mv.month) BETWEEN ? AND ?
                       THEN 1 END) AS recent_months,
            COALESCE(SUM(CASE WHEN (mv.year*100+mv.month) BETWEEN ? AND ?
                              THEN mv.views END), 0) AS baseline_sum,
            COALESCE(SUM(CASE WHEN (mv.year*100+mv.month) BETWEEN ? AND ?
                              THEN mv.views END), 0) AS recent_sum
        FROM samples s
        LEFT JOIN monthly_views mv ON mv.title = s.title
        GROUP BY s.title, s.primary_topic
        ORDER BY s.title
    """
    params = [
        baseline_lo, baseline_hi, recent_lo, recent_hi,
        baseline_lo, baseline_hi, recent_lo, recent_hi,
    ]
    with vital_db.get_connection(db_path) as conn:
        raw = conn.execute(query, params).fetchall()

    rows: list[dict] = []
    skipped = 0
    for r in raw:
        if (
            r["baseline_months"] != BASELINE_MONTHS_EXPECTED
            or r["recent_months"] != RECENT_MONTHS_EXPECTED
            or r["baseline_sum"] == 0
        ):
            skipped += 1
            continue
        bp = r["baseline_sum"] / BASELINE_MONTHS_EXPECTED
        rp = r["recent_sum"] / RECENT_MONTHS_EXPECTED
        rows.append({
            "title": r["title"],
            "primary_topic": r["primary_topic"],
            "baseline_per_month": bp,
            "recent_per_month": rp,
            "pct_change": (rp - bp) * 100.0 / bp,
        })
    return rows, skipped


def summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "median": None, "p25": None, "p75": None}
    s = sorted(values)
    q1, _, q3 = statistics.quantiles(s, n=4, method="inclusive")
    return {
        "n": len(s),
        "median": statistics.median(s),
        "p25": q1,
        "p75": q3,
    }


def bucket_summaries(rows: list[dict]) -> list[dict]:
    """One summary row per primary_topic bucket, plus an 'ALL' roll-up."""
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_bucket[r["primary_topic"]].append(r)

    out: list[dict] = []
    for bucket in sorted(by_bucket.keys()):
        items = by_bucket[bucket]
        pct_values = [i["pct_change"] for i in items]
        summary = summarize(pct_values)

        baseline_sum = sum(i["baseline_per_month"] for i in items)
        recent_sum = sum(i["recent_per_month"] for i in items)
        view_weighted_pct = (
            (recent_sum - baseline_sum) * 100.0 / baseline_sum
            if baseline_sum else None
        )

        frac_down_50 = (
            sum(1 for v in pct_values if v <= -50) / len(pct_values)
            if pct_values else 0.0
        )

        out.append({
            "bucket": bucket,
            "n": summary["n"],
            "median_pct": summary["median"],
            "p25_pct": summary["p25"],
            "p75_pct": summary["p75"],
            "view_weighted_pct": view_weighted_pct,
            "fraction_down_50pct": frac_down_50,
            "baseline_monthly_total": baseline_sum,
            "recent_monthly_total": recent_sum,
        })

    # Overall roll-up row.
    if rows:
        pct_values = [r["pct_change"] for r in rows]
        summary = summarize(pct_values)
        baseline_sum = sum(r["baseline_per_month"] for r in rows)
        recent_sum = sum(r["recent_per_month"] for r in rows)
        out.append({
            "bucket": "ALL",
            "n": summary["n"],
            "median_pct": summary["median"],
            "p25_pct": summary["p25"],
            "p75_pct": summary["p75"],
            "view_weighted_pct": (
                (recent_sum - baseline_sum) * 100.0 / baseline_sum
                if baseline_sum else None
            ),
            "fraction_down_50pct": (
                sum(1 for v in pct_values if v <= -50) / len(pct_values)
                if pct_values else 0.0
            ),
            "baseline_monthly_total": baseline_sum,
            "recent_monthly_total": recent_sum,
        })
    return out


def _fmt_pct(v: float | None) -> str:
    return f"{v:+6.1f}%" if v is not None else "    n/a"


def print_bucket_table(summaries: list[dict]) -> None:
    print()
    print("Per-bucket decline: 2016-19 vs 2025-04..2026-03 per-month averages")
    print("=" * 110)
    print(f"{'Bucket':<42} {'n':>5} {'median':>8} {'p25':>8} {'p75':>8} "
          f"{'view-wt':>8} {'≥50%↓':>7}")
    print("-" * 110)
    for s in summaries:
        print(
            f"{s['bucket']:<42} {s['n']:>5} "
            f"{_fmt_pct(s['median_pct']):>8} "
            f"{_fmt_pct(s['p25_pct']):>8} "
            f"{_fmt_pct(s['p75_pct']):>8} "
            f"{_fmt_pct(s['view_weighted_pct']):>8} "
            f"{s['fraction_down_50pct']*100:>6.1f}%"
        )
    print("-" * 110)
    print("  median/p25/p75: percent change in per-month-average pageviews, "
          "per article, within bucket.")
    print("  view-wt:        same, but weighted by pre-COVID pageview volume "
          "(one big article can dominate).")
    print("  ≥50%↓:         fraction of articles in the bucket that lost >=50%.")


def print_top_movers(rows: list[dict], n: int = 5) -> None:
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_bucket[r["primary_topic"]].append(r)

    print()
    print("Biggest decliners per bucket (top %d by absolute monthly-views lost):"
          % n)
    print("=" * 110)
    for bucket in sorted(by_bucket.keys()):
        items = sorted(
            by_bucket[bucket],
            key=lambda r: (r["recent_per_month"] - r["baseline_per_month"]),
        )[:n]
        if not items:
            continue
        print(f"\n{bucket}:")
        for r in items:
            print(
                f"  {r['title']:<60} "
                f"{r['baseline_per_month']:>10,.0f}/mo -> "
                f"{r['recent_per_month']:>10,.0f}/mo "
                f"({r['pct_change']:+6.1f}%)"
            )

    print()
    print("Most robust per bucket (top %d by pct growth or smallest decline):"
          % n)
    print("=" * 110)
    for bucket in sorted(by_bucket.keys()):
        items = sorted(
            by_bucket[bucket],
            key=lambda r: -r["pct_change"],
        )[:n]
        if not items:
            continue
        print(f"\n{bucket}:")
        for r in items:
            print(
                f"  {r['title']:<60} "
                f"{r['baseline_per_month']:>10,.0f}/mo -> "
                f"{r['recent_per_month']:>10,.0f}/mo "
                f"({r['pct_change']:+6.1f}%)"
            )


def print_provenance(
    db_path: Path | str,
    analyzed: int,
    skipped_partial: int,
) -> None:
    with vital_db.get_connection(db_path) as conn:
        (sample_n,) = conn.execute("SELECT COUNT(*) FROM samples").fetchone()
        (fetched_n,) = conn.execute(
            "SELECT COUNT(*) FROM pageview_fetch_log WHERE status = 'ok'"
        ).fetchone()
        (missing_n,) = conn.execute(
            "SELECT COUNT(*) FROM pageview_fetch_log WHERE status = 'missing'"
        ).fetchone()
        (error_n,) = conn.execute(
            "SELECT COUNT(*) FROM pageview_fetch_log WHERE status = 'error'"
        ).fetchone()
        (latest,) = conn.execute(
            "SELECT MAX(fetched_at) FROM pageview_fetch_log"
        ).fetchone()
        (seed_row,) = conn.execute(
            "SELECT MIN(seed) FROM samples"
        ).fetchone() or (None,)
    print()
    print("Provenance")
    print("-" * 70)
    print(f"  baseline window:  {BASELINE_START[0]}-{BASELINE_START[1]:02d} .. "
          f"{BASELINE_END[0]}-{BASELINE_END[1]:02d}  ({BASELINE_MONTHS_EXPECTED} months)")
    print(f"  recent window:    {RECENT_START[0]}-{RECENT_START[1]:02d} .. "
          f"{RECENT_END[0]}-{RECENT_END[1]:02d}  ({RECENT_MONTHS_EXPECTED} months)")
    print(f"  sample size:      {sample_n:,} articles  (seed: {seed_row})")
    print(f"  fetch log:        ok={fetched_n:,}  missing={missing_n:,}  "
          f"error={error_n:,}")
    print(f"  analyzed:         {analyzed:,} articles with full coverage in both windows")
    print(f"  skipped partial:  {skipped_partial:,} "
          "(partial coverage or zero baseline)")
    print(f"  latest fetched:   {latest or '(none)'}")


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["title", "primary_topic", "baseline_per_month",
               "recent_per_month", "pct_change"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r[k] for k in columns})


def run(
    db_path: Path | str = vital_db.DEFAULT_DB_PATH,
    output_csv: Path = DEFAULT_CSV,
    show_top_movers: bool = True,
) -> int:
    with vital_db.get_connection(db_path) as conn:
        (sample_n,) = conn.execute("SELECT COUNT(*) FROM samples").fetchone()
    if sample_n == 0:
        print("samples table is empty. Run sample.py first.", file=sys.stderr)
        return 1

    rows, skipped = compute_decline_rows(db_path=db_path)
    if not rows:
        print("No articles with full coverage in both windows. "
              "Did fetch_pageviews.py finish?", file=sys.stderr)
        print_provenance(db_path, analyzed=0, skipped_partial=skipped)
        return 1

    summaries = bucket_summaries(rows)
    print_bucket_table(summaries)
    if show_top_movers:
        print_top_movers(rows)
    print_provenance(db_path, analyzed=len(rows), skipped_partial=skipped)
    write_csv(rows, Path(output_csv))
    print(f"\nPer-article CSV: {output_csv}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=vital_db.DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--no-movers", action="store_true",
                        help="Skip the per-bucket top decliners/robust tables")
    args = parser.parse_args()
    return run(db_path=args.db, output_csv=args.output,
               show_top_movers=not args.no_movers)


if __name__ == "__main__":
    raise SystemExit(main())
