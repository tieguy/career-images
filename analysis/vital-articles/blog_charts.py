"""Generate matplotlib PNGs for the vital-articles blog post.

Parallel to analysis/career-cliff/blog_charts.py. Title-keyed because the
upstream Wikipedia:Vital_articles/data/*.json is title-keyed on en.wikipedia.
Style helpers are duplicated (not imported from career-cliff) so the two
subprojects can diverge independently.

Reads from vital.db. Writes to analysis/vital-articles/output/charts/.

Usage:
    uv sync --extra dev --extra analysis
    uv run python analysis/vital-articles/blog_charts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from statsmodels.nonparametric.smoothers_lowess import lowess

sys.path.insert(0, str(Path(__file__).parent))

import report  # noqa: E402
import vital_db  # noqa: E402

LOESS_SPAN_AGGREGATE = 0.15
RAW_COLOR = "#888888"
RAW_ALPHA = 0.35
BASELINE_BAND_COLOR = "#555555"
RECENT_BAND_COLOR = "#d62728"
BAND_ALPHA = 0.08

OUTPUT_DIR = Path(__file__).parent / "output" / "charts"

CHATGPT_LAUNCH_YM = (2022, 11)
COVID_PEAK_YM = (2020, 4)

BASELINE_WINDOW = (report.BASELINE_START, report.BASELINE_END)
RECENT_WINDOW = (report.RECENT_START, report.RECENT_END)


def _ym_to_float(year: int, month: int) -> float:
    return year + (month - 1) / 12.0


def setup_style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 150,
        "savefig.dpi": 150,
        "savefig.bbox": "tight",
    })


def _loess(xs: list[float], ys: list[float], frac: float) -> list[float]:
    return list(lowess(ys, xs, frac=frac, return_sorted=False))


def _shade_comparison_windows(ax, *, label: bool = False) -> None:
    b_lo = _ym_to_float(*BASELINE_WINDOW[0])
    b_hi = _ym_to_float(BASELINE_WINDOW[1][0], BASELINE_WINDOW[1][1] + 1)
    r_lo = _ym_to_float(*RECENT_WINDOW[0])
    r_hi = _ym_to_float(RECENT_WINDOW[1][0], RECENT_WINDOW[1][1] + 1)
    ax.axvspan(b_lo, b_hi, color=BASELINE_BAND_COLOR, alpha=BAND_ALPHA,
               label="Baseline window" if label else None)
    ax.axvspan(r_lo, r_hi, color=RECENT_BAND_COLOR, alpha=BAND_ALPHA,
               label="Recent window" if label else None)


def fetch_full_coverage_monthly_totals() -> dict[tuple[int, int], int]:
    """Sum monthly pageviews across sampled vital articles with full coverage
    in both comparison windows (same filter as report.compute_decline_rows).
    """
    b_lo = report._ym_key(*report.BASELINE_START)
    b_hi = report._ym_key(*report.BASELINE_END)
    r_lo = report._ym_key(*report.RECENT_START)
    r_hi = report._ym_key(*report.RECENT_END)
    coverage_q = f"""
        SELECT mv.title FROM monthly_views mv
        JOIN samples s ON s.title = mv.title
        GROUP BY mv.title
        HAVING
            SUM(CASE WHEN (year*100+month) BETWEEN {b_lo} AND {b_hi}
                     THEN 1 ELSE 0 END) = {report.BASELINE_MONTHS_EXPECTED}
          AND SUM(CASE WHEN (year*100+month) BETWEEN {r_lo} AND {r_hi}
                     THEN 1 ELSE 0 END) = {report.RECENT_MONTHS_EXPECTED}
          AND SUM(CASE WHEN (year*100+month) BETWEEN {b_lo} AND {b_hi}
                     THEN views ELSE 0 END) > 0
    """
    sum_q = f"""
        SELECT year, month, SUM(views) AS total
        FROM monthly_views
        WHERE title IN ({coverage_q})
        GROUP BY year, month
        ORDER BY year, month
    """
    with vital_db.get_connection() as conn:
        return {(r["year"], r["month"]): r["total"] for r in conn.execute(sum_q).fetchall()}


def _count_full_coverage_articles() -> int:
    rows, _ = report.compute_decline_rows()
    return len(rows)


def chart_bucket_decline(
    summaries: list[dict],
    path: Path = OUTPUT_DIR / "01_bucket_decline.png",
) -> None:
    """Horizontal bar chart of per-bucket median decline, sorted worst-first.

    One bar per editorial bucket, plus an 'ALL' summary bar styled separately.
    Whiskers show p25..p75 spread. Right-side annotation gives n and the
    view-weighted decline (same bucket, weighted by pre-COVID pageview volume).
    """
    bucket_rows = [s for s in summaries if s["bucket"] != "ALL"]
    all_row = next((s for s in summaries if s["bucket"] == "ALL"), None)
    bucket_rows.sort(key=lambda s: s["median_pct"])

    rows = bucket_rows + ([all_row] if all_row else [])
    labels = [s["bucket"] for s in rows]
    medians = [s["median_pct"] for s in rows]
    p25 = [s["p25_pct"] for s in rows]
    p75 = [s["p75_pct"] for s in rows]

    fig, ax = plt.subplots(figsize=(9, 5.5))
    y = list(range(len(rows)))

    colors = ["#d62728" if s["bucket"] != "ALL" else "#333333" for s in rows]
    ax.barh(y, medians, color=colors, alpha=0.75, height=0.7, zorder=3)

    xerr_lo = [m - lo for m, lo in zip(medians, p25)]
    xerr_hi = [hi - m for m, hi in zip(medians, p75)]
    ax.errorbar(
        medians, y, xerr=[xerr_lo, xerr_hi],
        fmt="none", ecolor="#444444", elinewidth=1.0, capsize=3, zorder=4,
    )

    ax.axvline(0, color="#555555", linewidth=0.8, zorder=2)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))
    ax.set_xlabel("Change in per-month-average pageviews (median across sampled articles)")

    xmin, xmax = ax.get_xlim()
    annot_x = xmax + (xmax - xmin) * 0.02
    for yi, s in enumerate(rows):
        ann = (
            f"n={s['n']:,}   "
            f"view-wt {s['view_weighted_pct']:+.1f}%   "
            f"≥50%↓ {s['fraction_down_50pct'] * 100:.0f}%"
        )
        ax.text(annot_x, yi, ann, va="center", fontsize=8.5, color="#333333")
    ax.set_xlim(xmin, xmax + (xmax - xmin) * 0.40)

    fig.suptitle(
        "Vital Articles (Level 5): decline by editorial topic",
        fontsize=13, fontweight="bold", y=0.995,
    )
    ax.set_title(
        "Bars: median per-article % change (2016–19 vs 2025-04 through 2026-03).  "
        "Whiskers: p25–p75.",
        fontsize=9.5, color="#444444", loc="center", pad=6,
    )
    fig.text(
        0.5, 0.02,
        "Stratified random sample of 5,000 Level-5 Vital Articles.  "
        "4,826 analyzed after dropping partial-coverage / zero-baseline.  "
        "‘≥50%↓’: share of articles in bucket that lost at least half their monthly pageviews.",
        ha="center", fontsize=7.5, color="#666666",
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    print(f"Wrote {path}")


def chart_aggregate_traffic(
    path: Path = OUTPUT_DIR / "02_aggregate_traffic.png",
) -> None:
    """Aggregate monthly pageviews across all 4,826 full-coverage vital articles.

    Parallel to career-cliff/01_aggregate_traffic.png: raw monthly totals,
    LOESS trend, shaded comparison windows, COVID + ChatGPT anchors.
    """
    totals = fetch_full_coverage_monthly_totals()
    if not totals:
        raise SystemExit("No full-coverage vital articles found — has fetch_pageviews run?")
    n_articles = _count_full_coverage_articles()

    keys = sorted(totals.keys())
    xs = [_ym_to_float(y, m) for (y, m) in keys]
    ys = [totals[k] for k in keys]
    smoothed = _loess(xs, ys, LOESS_SPAN_AGGREGATE)

    fig, ax = plt.subplots(figsize=(10, 5))

    _shade_comparison_windows(ax, label=True)

    ys_m = [v / 1e6 for v in ys]
    smoothed_m = [v / 1e6 for v in smoothed]
    ax.plot(xs, ys_m, color=RAW_COLOR, alpha=RAW_ALPHA, linewidth=1.0,
            label="Monthly pageviews (raw)")
    ax.plot(xs, smoothed_m, color="#1f77b4", linewidth=2.2,
            label="LOESS-smoothed trend")

    covid_x = _ym_to_float(*COVID_PEAK_YM)
    chatgpt_x = _ym_to_float(*CHATGPT_LAUNCH_YM)
    ax.axvline(covid_x, color="#555555", linestyle=":", linewidth=1.0, alpha=0.7)
    ax.axvline(chatgpt_x, color=RECENT_BAND_COLOR, linestyle="--", linewidth=1.0, alpha=0.75)

    ymin, ymax = ax.get_ylim()
    label_y = ymin + (ymax - ymin) * 0.92
    ax.text(covid_x + 0.05, label_y, "COVID\nlockdowns",
            fontsize=8, color="#555555", va="top")
    ax.text(chatgpt_x + 0.05, label_y, "ChatGPT\nlaunches",
            fontsize=8, color=RECENT_BAND_COLOR, va="top")

    ax.set_xlabel("Year")
    ax.set_ylabel("Monthly pageviews (M)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}M"))
    ax.legend(loc="lower left", fontsize=8, frameon=False)

    fig.suptitle(
        f"Aggregate monthly pageviews: Vital Articles (n = {n_articles:,})",
        fontsize=13, fontweight="bold", y=0.995,
    )
    ax.set_title(
        "Sum across full-coverage sampled articles.  "
        "Grey band = baseline (2016–19).  Red band = recent (2025-04 through 2026-03).",
        fontsize=9.5, color="#444444", pad=6,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    print(f"Wrote {path}")


def main() -> int:
    setup_style()
    rows, _ = report.compute_decline_rows()
    summaries = report.bucket_summaries(rows)
    chart_bucket_decline(summaries)
    chart_aggregate_traffic()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
