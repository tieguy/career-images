"""Generate matplotlib PNGs for the blog post.

Reads from history.db. Writes to analysis/historical-decline/output/charts/.

Usage:
    uv sync --extra dev --extra analysis
    uv run python analysis/historical-decline/blog_charts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

sys.path.insert(0, str(Path(__file__).parent))

import history_db  # noqa: E402
import report  # noqa: E402

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


def fetch_yoy_medians() -> list[tuple[int, float, int]]:
    """Median year-over-year % change by year, across full-coverage ever-top articles.

    Returns [(year, median_pct, n), ...] for years 2017..2025.
    """
    query = """
        WITH fullcov AS (
            SELECT wikidata_id FROM annual_totals
            WHERE wikidata_id IN (SELECT wikidata_id FROM ever_top)
            GROUP BY wikidata_id
            HAVING COUNT(DISTINCT year) = 10
        ),
        pairs AS (
            SELECT
                a.wikidata_id, b.year AS this_year,
                CASE WHEN a.views > 0
                     THEN (b.views - a.views) * 100.0 / a.views
                     ELSE NULL END AS yoy
            FROM annual_totals a
            JOIN annual_totals b ON a.wikidata_id = b.wikidata_id AND b.year = a.year + 1
            WHERE a.wikidata_id IN (SELECT wikidata_id FROM fullcov)
        )
        SELECT this_year, yoy FROM pairs WHERE yoy IS NOT NULL ORDER BY this_year, yoy
    """
    with history_db.get_connection() as conn:
        all_rows = conn.execute(query).fetchall()

    from collections import defaultdict
    import statistics
    groups: dict[int, list[float]] = defaultdict(list)
    for r in all_rows:
        groups[r["this_year"]].append(r["yoy"])
    return [
        (year, statistics.median(vals), len(vals))
        for year, vals in sorted(groups.items())
    ]


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def chart_yoy_cliff(yoy: list[tuple[int, float, int]], path: Path) -> None:
    years = [y for (y, _, _) in yoy]
    medians = [m for (_, m, _) in yoy]
    colors = ["#d62728" if y == 2025 else "#4a6fa5" for y in years]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(years, medians, color=colors, edgecolor="black", linewidth=0.5)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(years)
    ax.set_ylabel("Median year-over-year % change")
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))
    ax.set_title("The 2025 cliff: median year-over-year pageview change")
    ax.text(
        0.02, 0.03,
        f"n = {yoy[0][2]} career articles with full 2016–2025 coverage",
        transform=ax.transAxes, fontsize=9, color="#555555",
    )
    # Annotate 2025 directly.
    val_2025 = medians[-1]
    ax.annotate(
        f"{val_2025:+.1f}%",
        xy=(2025, val_2025),
        xytext=(0, -18 if val_2025 < 0 else 8),
        textcoords="offset points",
        ha="center", fontsize=10, fontweight="bold", color="#d62728",
    )
    fig.savefig(path)
    plt.close(fig)


def chart_aggregate_traffic(
    rows: list[dict], monthly_series: dict[str, list[tuple[int, int, int]]],
    path: Path,
) -> None:
    """Sum monthly views across all full-coverage articles."""
    totals_by_month: dict[tuple[int, int], int] = {}
    for qid, series in monthly_series.items():
        for (year, month, views) in series:
            totals_by_month[(year, month)] = totals_by_month.get((year, month), 0) + views
    months = sorted(totals_by_month.keys())
    xs = [_ym_to_float(y, m) for (y, m) in months]
    ys = [totals_by_month[m] / 1_000_000 for m in months]

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(xs, ys, color="#4a6fa5", linewidth=1.6)
    ax.fill_between(xs, ys, alpha=0.15, color="#4a6fa5")

    ax.axvline(_ym_to_float(*CHATGPT_LAUNCH_YM), color="#d62728",
               linestyle="--", linewidth=1, alpha=0.7)
    ax.text(_ym_to_float(*CHATGPT_LAUNCH_YM) + 0.05,
            max(ys) * 0.92, "ChatGPT\nlaunches",
            fontsize=9, color="#d62728", va="top")

    ax.axvline(_ym_to_float(*COVID_PEAK_YM), color="#888888",
               linestyle=":", linewidth=1, alpha=0.7)
    ax.text(_ym_to_float(*COVID_PEAK_YM) + 0.05,
            max(ys) * 0.92, "COVID\nlockdowns",
            fontsize=9, color="#666666", va="top")

    ax.set_xlabel("Year")
    ax.set_ylabel("Total monthly pageviews (millions)")
    ax.set_title(
        f"Aggregate monthly pageviews across {len(rows)} top career articles"
    )
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}M"))
    fig.savefig(path)
    plt.close(fig)


def chart_distribution(rows: list[dict], path: Path) -> None:
    import statistics
    pct = sorted(r["pct_change"] for r in rows)
    median = statistics.median(pct)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    bins = list(range(-100, 101, 10))  # -100 to +100 in 10-point bins
    ax.hist(pct, bins=bins, color="#4a6fa5", edgecolor="white", linewidth=0.5)
    ax.axvline(median, color="#d62728", linestyle="--", linewidth=1.5)
    ax.text(
        median + 2, ax.get_ylim()[1] * 0.88,
        f"median {median:+.1f}%",
        color="#d62728", fontsize=10, fontweight="bold",
    )
    ax.axvline(0, color="black", linewidth=0.8, alpha=0.4)
    ax.set_xlabel("Percent change, per-month average (2016–19 → 2025 + Q1 2026)")
    ax.set_ylabel("Number of articles")
    ax.xaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))
    ax.set_title(
        f"Distribution of pageview change across {len(rows)} career articles"
    )
    fig.savefig(path)
    plt.close(fig)


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
        ax.plot(xs, ys, color="#4a6fa5", linewidth=1.2)
        ax.axvline(chatgpt_x, color="#d62728", linestyle="--",
                   linewidth=0.8, alpha=0.6)
        ax.set_title(
            f"{_pretty_title(row['title'])}\n({row['pct_change']:+.0f}%)",
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
        "Red dashed line marks ChatGPT launch (Nov 2022). Y-axes unlabeled; "
        "per-article scales differ substantially.",
        ha="center", fontsize=8, color="#888888",
    )
    fig.tight_layout(rect=[0, 0.03, 1, 0.92])
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

    print(f"Loading YoY medians...", flush=True)
    yoy = fetch_yoy_medians()

    # Selections for small multiples.
    pre_covid_leaders = sorted(rows, key=lambda r: -r["baseline_per_month"])[:10]
    biggest_decliners = sorted(rows, key=lambda r: r["pct_change"])[:10]
    most_robust = sorted(rows, key=lambda r: -r["pct_change"])[:10]

    print("Rendering charts...", flush=True)
    chart_yoy_cliff(yoy, OUTPUT_DIR / "01_yoy_cliff.png")
    chart_aggregate_traffic(rows, monthly_series, OUTPUT_DIR / "02_aggregate_traffic.png")
    chart_distribution(rows, OUTPUT_DIR / "03_distribution.png")
    chart_small_multiples(
        pre_covid_leaders, monthly_series,
        OUTPUT_DIR / "04a_pre_covid_leaders.png",
        "Pre-COVID leaders: then and now",
        "The 10 career articles with highest 2016–2019 monthly traffic",
    )
    chart_small_multiples(
        biggest_decliners, monthly_series,
        OUTPUT_DIR / "04b_biggest_decliners.png",
        "Biggest decliners",
        "The 10 career articles with the steepest per-month % drop",
    )
    chart_small_multiples(
        most_robust, monthly_series,
        OUTPUT_DIR / "04c_most_robust.png",
        "Most robust",
        "The 10 career articles that held up best (least decline / most growth)",
    )

    print(f"Done. Charts written to {OUTPUT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
