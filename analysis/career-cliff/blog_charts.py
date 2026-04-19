"""Generate matplotlib PNGs for the blog post.

Reads from history.db. Writes to analysis/career-cliff/output/charts/.

Usage:
    uv sync --extra dev --extra analysis
    uv run python analysis/career-cliff/blog_charts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from statsmodels.nonparametric.smoothers_lowess import lowess

sys.path.insert(0, str(Path(__file__).parent))

import history_db  # noqa: E402
import report  # noqa: E402

LOESS_SPAN_AGGREGATE = 0.15
LOESS_SPAN_SMALL_MULTIPLES = 0.25
RAW_COLOR = "#888888"
RAW_ALPHA = 0.35
BASELINE_BAND_COLOR = "#555555"
RECENT_BAND_COLOR = "#d62728"
BAND_ALPHA = 0.08

OUTPUT_DIR = Path(__file__).parent / "output" / "charts"

# LLM-era anchor: ChatGPT public release.
CHATGPT_LAUNCH_YM = (2022, 11)
COVID_PEAK_YM = (2020, 4)

BASELINE_WINDOW = (report.BASELINE_START, report.BASELINE_END)
RECENT_WINDOW = (report.RECENT_START, report.RECENT_END)


def _ym_to_float(year: int, month: int) -> float:
    """Encode (year, month) as a decimal year for plotting on a continuous x-axis."""
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
    """Draw translucent bands over the baseline and recent comparison windows."""
    b_lo = _ym_to_float(*BASELINE_WINDOW[0])
    b_hi = _ym_to_float(BASELINE_WINDOW[1][0], BASELINE_WINDOW[1][1] + 1)
    r_lo = _ym_to_float(*RECENT_WINDOW[0])
    r_hi = _ym_to_float(RECENT_WINDOW[1][0], RECENT_WINDOW[1][1] + 1)
    ax.axvspan(b_lo, b_hi, color=BASELINE_BAND_COLOR, alpha=BAND_ALPHA,
               label="Baseline window" if label else None)
    ax.axvspan(r_lo, r_hi, color=RECENT_BAND_COLOR, alpha=BAND_ALPHA,
               label="Recent window" if label else None)


def fetch_decline_rows() -> list[dict]:
    """Load full-coverage decline rows (106 articles on the live dataset)."""
    return report.compute_decline_rows()


def fetch_monthly_series(wikidata_ids: list[str]) -> dict[str, list[tuple[int, int, int]]]:
    """Return {wikidata_id: [(year, month, views), ...]} ordered chronologically."""
    if not wikidata_ids:
        return {}
    placeholders = ",".join("?" for _ in wikidata_ids)
    query = f"""
        SELECT wikidata_id, year, month, views
        FROM monthly_views
        WHERE wikidata_id IN ({placeholders})
        ORDER BY wikidata_id, year, month
    """
    out: dict[str, list[tuple[int, int, int]]] = {qid: [] for qid in wikidata_ids}
    with history_db.get_connection() as conn:
        for row in conn.execute(query, wikidata_ids).fetchall():
            out[row["wikidata_id"]].append((row["year"], row["month"], row["views"]))
    return out


def fetch_all_articles_monthly_totals() -> dict[tuple[int, int], int]:
    """Sum monthly pageviews across all full-window-coverage career articles.

    Uses the same coverage filter as fetch_all_article_decline_pct: articles
    with complete months in both the baseline (48 mo) and recent (12 mo)
    windows. Interregnum months may be partial per-article; the aggregate is
    still dominated by high-traffic articles and traces the overall trend.
    """
    from report import (
        _ym_key,
        BASELINE_START, BASELINE_END, RECENT_START, RECENT_END,
        BASELINE_MONTHS_EXPECTED, RECENT_MONTHS_EXPECTED,
    )
    b_lo, b_hi = _ym_key(*BASELINE_START), _ym_key(*BASELINE_END)
    r_lo, r_hi = _ym_key(*RECENT_START), _ym_key(*RECENT_END)
    coverage_q = f"""
        SELECT wikidata_id FROM monthly_views
        GROUP BY wikidata_id
        HAVING
            SUM(CASE WHEN (year*100+month) BETWEEN {b_lo} AND {b_hi}
                     THEN 1 ELSE 0 END) = {BASELINE_MONTHS_EXPECTED}
          AND SUM(CASE WHEN (year*100+month) BETWEEN {r_lo} AND {r_hi}
                     THEN 1 ELSE 0 END) = {RECENT_MONTHS_EXPECTED}
    """
    sum_q = f"""
        SELECT year, month, SUM(views) AS total
        FROM monthly_views
        WHERE wikidata_id IN ({coverage_q})
        GROUP BY year, month
        ORDER BY year, month
    """
    with history_db.get_connection() as conn:
        rows = conn.execute(sum_q).fetchall()
    return {(r["year"], r["month"]): r["total"] for r in rows}


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _render_aggregate_panel(
    ax,
    totals: dict[tuple[int, int], int],
    title: str,
    n_articles: int,
) -> float:
    """Render one aggregate-traffic panel; returns the max raw y-value (in M)."""
    months = sorted(totals.keys())
    xs = [_ym_to_float(y, m) for (y, m) in months]
    ys = [totals[m] / 1_000_000 for m in months]
    smooth = _loess(xs, ys, frac=LOESS_SPAN_AGGREGATE)

    _shade_comparison_windows(ax, label=True)
    ax.plot(xs, ys, color=RAW_COLOR, linewidth=0.9, alpha=RAW_ALPHA,
            label="Monthly pageviews (raw)")
    ax.plot(xs, smooth, color="#4a6fa5", linewidth=2.0,
            label="LOESS-smoothed trend")
    ax.axvline(_ym_to_float(*CHATGPT_LAUNCH_YM), color="#d62728",
               linestyle="--", linewidth=1, alpha=0.7)
    ax.axvline(_ym_to_float(*COVID_PEAK_YM), color="#888888",
               linestyle=":", linewidth=1, alpha=0.7)
    ax.set_ylabel("Monthly pageviews (M)")
    ax.set_title(f"{title}  (n = {n_articles:,})", fontsize=11)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(
        lambda v, _: f"{v:.0f}M" if v == int(v) else f"{v:.1f}M"
    ))
    return max(ys)


def chart_aggregate_traffic(
    rows: list[dict],
    monthly_series: dict[str, list[tuple[int, int, int]]],
    all_totals: dict[tuple[int, int], int],
    path: Path,
) -> None:
    """Two-panel: top-article aggregate and full-career-set aggregate."""
    top_totals: dict[tuple[int, int], int] = {}
    for _qid, series in monthly_series.items():
        for (year, month, views) in series:
            top_totals[(year, month)] = top_totals.get((year, month), 0) + views

    fig, (ax_top, ax_all) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    top_max = _render_aggregate_panel(
        ax_top, top_totals, "Top career articles", len(rows),
    )
    n_full = _count_full_coverage_articles()
    _render_aggregate_panel(
        ax_all, all_totals,
        "All career articles (full-window coverage)", n_full,
    )

    ax_top.legend(loc="lower left", frameon=False, fontsize=8)
    ax_top.text(_ym_to_float(*CHATGPT_LAUNCH_YM) + 0.05, top_max * 0.92,
                "ChatGPT\nlaunches", fontsize=9, color="#d62728", va="top")
    ax_top.text(_ym_to_float(*COVID_PEAK_YM) + 0.05, top_max * 0.92,
                "COVID\nlockdowns", fontsize=9, color="#666666", va="top")

    ax_all.set_xlabel("Year")
    fig.suptitle(
        "Aggregate monthly pageviews: top articles vs the full career set",
        fontsize=13, fontweight="bold",
    )
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(path)
    plt.close(fig)


def _count_full_coverage_articles() -> int:
    from report import (
        _ym_key,
        BASELINE_START, BASELINE_END, RECENT_START, RECENT_END,
        BASELINE_MONTHS_EXPECTED, RECENT_MONTHS_EXPECTED,
    )
    b_lo, b_hi = _ym_key(*BASELINE_START), _ym_key(*BASELINE_END)
    r_lo, r_hi = _ym_key(*RECENT_START), _ym_key(*RECENT_END)
    q = f"""
        SELECT COUNT(*) AS n FROM (
            SELECT wikidata_id FROM monthly_views
            GROUP BY wikidata_id
            HAVING
                SUM(CASE WHEN (year*100+month) BETWEEN {b_lo} AND {b_hi}
                         THEN 1 ELSE 0 END) = {BASELINE_MONTHS_EXPECTED}
              AND SUM(CASE WHEN (year*100+month) BETWEEN {r_lo} AND {r_hi}
                         THEN 1 ELSE 0 END) = {RECENT_MONTHS_EXPECTED}
        )
    """
    with history_db.get_connection() as conn:
        return conn.execute(q).fetchone()["n"]


def _pretty_title(title: str) -> str:
    return title.replace("_", " ")


def chart_small_multiples(
    selection: list[dict],
    monthly_series: dict[str, list[tuple[int, int, int]]],
    path: Path,
    big_title: str,
    subtitle: str,
) -> None:
    """Render a 2×5 grid of monthly time series, one per article in `selection`."""
    assert len(selection) == 10
    fig, axes = plt.subplots(2, 5, figsize=(14, 6.5), sharex=True)
    chatgpt_x = _ym_to_float(*CHATGPT_LAUNCH_YM)

    for ax, row in zip(axes.flatten(), selection):
        qid = row["wikidata_id"]
        series = monthly_series.get(qid, [])
        xs = [_ym_to_float(y, m) for (y, m, _) in series]
        ys = [v for (_, _, v) in series]
        _shade_comparison_windows(ax)
        ax.plot(xs, ys, color=RAW_COLOR, linewidth=0.7, alpha=RAW_ALPHA)
        if len(xs) >= 4:
            smooth = _loess(xs, ys, frac=LOESS_SPAN_SMALL_MULTIPLES)
            ax.plot(xs, smooth, color="#4a6fa5", linewidth=1.4)
        ax.axvline(chatgpt_x, color="#d62728", linestyle="--",
                   linewidth=0.8, alpha=0.6)
        ax.set_title(
            f"{_pretty_title(row['title'])}\n"
            f"avg monthly: {row['pct_change']:+.0f}%",
            fontsize=9,
        )
        ax.set_ylim(bottom=0)
        ax.set_yticks([])  # per-article scales differ; hide to reduce clutter
        ax.tick_params(axis="x", labelsize=8)
        ax.grid(axis="y", alpha=0.25)

    fig.suptitle(big_title, fontsize=14, fontweight="bold", y=0.99)
    fig.text(
        0.5, 0.94, subtitle,
        ha="center", fontsize=10, color="#555555",
    )
    fig.text(
        0.5, 0.01,
        "Grey band = baseline window (2016–2019, 48 mo).   "
        "Red band = recent window (2025-04…2026-03, 12 mo).   "
        "% change compares per-month averages across those two windows.\n"
        "Grey line = raw monthly pageviews.   "
        "Blue = LOESS-smoothed trend.   "
        "Red dashed line = ChatGPT launch (Nov 2022).   "
        "Y-axes unlabeled; per-article scales differ.",
        ha="center", fontsize=8, color="#888888",
    )
    fig.tight_layout(rect=[0, 0.05, 1, 0.92])
    fig.savefig(path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    setup_style()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading decline data...", flush=True)
    rows = fetch_decline_rows()
    print(f"  {len(rows)} full-coverage articles", flush=True)

    all_qids = [r["wikidata_id"] for r in rows]
    print(f"Loading monthly series for {len(all_qids)} articles...", flush=True)
    monthly_series = fetch_monthly_series(all_qids)

    print(f"Loading full-set monthly totals...", flush=True)
    all_monthly_totals = fetch_all_articles_monthly_totals()

    # Selections for small multiples.
    pre_covid_leaders = sorted(rows, key=lambda r: -r["baseline_per_month"])[:10]
    biggest_decliners = sorted(rows, key=lambda r: r["pct_change"])[:10]
    most_robust = sorted(rows, key=lambda r: -r["pct_change"])[:10]

    print("Rendering charts...", flush=True)
    chart_aggregate_traffic(rows, monthly_series, all_monthly_totals,
                            OUTPUT_DIR / "01_aggregate_traffic.png")
    chart_small_multiples(
        pre_covid_leaders, monthly_series,
        OUTPUT_DIR / "03a_pre_covid_leaders.png",
        "Pre-COVID leaders: then and now",
        "The 10 career articles with highest 2016–2019 monthly traffic",
    )
    chart_small_multiples(
        biggest_decliners, monthly_series,
        OUTPUT_DIR / "03b_biggest_decliners.png",
        "Biggest decliners",
        "The 10 career articles with the steepest per-month % drop",
    )
    chart_small_multiples(
        most_robust, monthly_series,
        OUTPUT_DIR / "03c_most_robust.png",
        "Most robust",
        "The 10 career articles that held up best (least decline / most growth)",
    )

    print(f"Done. Charts written to {OUTPUT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
