"""Cross-language decline charts for the Vital Articles sample.

Compares per-language decline across Wikipedia editions for the same set of
Wikidata items. Reads en.wikipedia pageviews from monthly_views (title-keyed,
joined to samples.wikidata_id) and non-en from cross_lang_monthly_views
(qid-keyed directly).

Only languages that have fetched data are rendered — run
fetch_cross_lang_pageviews.py for more languages to extend the chart set.

Usage:
    uv sync --extra dev --extra analysis
    uv run python analysis/vital-articles/cross_lang_charts.py
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from statsmodels.nonparametric.smoothers_lowess import lowess

sys.path.insert(0, str(Path(__file__).parent))

import report  # noqa: E402
import vital_db  # noqa: E402

OUTPUT_DIR = Path(__file__).parent / "output" / "charts"

LOESS_SPAN = 0.2
RAW_COLOR = "#888888"
RAW_ALPHA = 0.30
BASELINE_BAND_COLOR = "#555555"
RECENT_BAND_COLOR = "#d62728"
BAND_ALPHA = 0.08

CHATGPT_LAUNCH_YM = (2022, 11)
COVID_PEAK_YM = (2020, 4)

BASELINE_START, BASELINE_END = report.BASELINE_START, report.BASELINE_END
RECENT_START, RECENT_END = report.RECENT_START, report.RECENT_END
BASELINE_MONTHS_EXPECTED = report.BASELINE_MONTHS_EXPECTED
RECENT_MONTHS_EXPECTED = report.RECENT_MONTHS_EXPECTED

# Pleasant, print-safe palette for up to ~12 lines.
LANGUAGE_COLORS = {
    "en": "#1f77b4",
    "es": "#d62728",
    "fr": "#2ca02c",
    "de": "#9467bd",
    "zh": "#ff7f0e",
    "ru": "#8c564b",
    "it": "#e377c2",
    "ar": "#17becf",
    "pt": "#bcbd22",
    "fa": "#7f7f7f",
    "ja": "#1a9850",
    "uk": "#66a61e",
}

# Human-readable labels for the legend.
LANGUAGE_LABELS = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "zh": "Chinese",
    "ru": "Russian",
    "it": "Italian",
    "ar": "Arabic",
    "pt": "Portuguese",
    "fa": "Persian",
    "ja": "Japanese",
    "uk": "Ukrainian",
}


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


def _shade_comparison_windows(ax, *, label: bool = False) -> None:
    b_lo = _ym_to_float(*BASELINE_START)
    b_hi = _ym_to_float(BASELINE_END[0], BASELINE_END[1] + 1)
    r_lo = _ym_to_float(*RECENT_START)
    r_hi = _ym_to_float(RECENT_END[0], RECENT_END[1] + 1)
    ax.axvspan(b_lo, b_hi, color=BASELINE_BAND_COLOR, alpha=BAND_ALPHA,
               label="Baseline window" if label else None)
    ax.axvspan(r_lo, r_hi, color=RECENT_BAND_COLOR, alpha=BAND_ALPHA,
               label="Recent window" if label else None)


def _available_non_en_languages() -> list[str]:
    with vital_db.get_connection() as conn:
        rows = conn.execute("""
            SELECT language, COUNT(DISTINCT qid) AS n
            FROM cross_lang_monthly_views
            GROUP BY language
            ORDER BY n DESC
        """).fetchall()
    return [r["language"] for r in rows if r["n"] >= 100]


def _decline_rows_en() -> list[tuple[str, float, float]]:
    """Return [(qid, baseline_per_month, recent_per_month), ...] for en.

    Joins samples.wikidata_id with monthly_views on title so results are
    QID-keyed like the non-en data.
    """
    b_lo = report._ym_key(*BASELINE_START)
    b_hi = report._ym_key(*BASELINE_END)
    r_lo = report._ym_key(*RECENT_START)
    r_hi = report._ym_key(*RECENT_END)
    query = f"""
        SELECT
            s.wikidata_id AS qid,
            COUNT(CASE WHEN (mv.year*100+mv.month) BETWEEN {b_lo} AND {b_hi}
                       THEN 1 END) AS baseline_months,
            COUNT(CASE WHEN (mv.year*100+mv.month) BETWEEN {r_lo} AND {r_hi}
                       THEN 1 END) AS recent_months,
            COALESCE(SUM(CASE WHEN (mv.year*100+mv.month) BETWEEN {b_lo} AND {b_hi}
                              THEN mv.views END), 0) AS baseline_sum,
            COALESCE(SUM(CASE WHEN (mv.year*100+mv.month) BETWEEN {r_lo} AND {r_hi}
                              THEN mv.views END), 0) AS recent_sum
        FROM samples s
        JOIN monthly_views mv ON mv.title = s.title
        WHERE s.wikidata_id IS NOT NULL
        GROUP BY s.wikidata_id
    """
    out: list[tuple[str, float, float]] = []
    with vital_db.get_connection() as conn:
        for r in conn.execute(query).fetchall():
            if (r["baseline_months"] != BASELINE_MONTHS_EXPECTED
                    or r["recent_months"] != RECENT_MONTHS_EXPECTED
                    or r["baseline_sum"] == 0):
                continue
            out.append((
                r["qid"],
                r["baseline_sum"] / BASELINE_MONTHS_EXPECTED,
                r["recent_sum"] / RECENT_MONTHS_EXPECTED,
            ))
    return out


def _decline_rows_lang(language: str) -> list[tuple[str, float, float]]:
    """Same shape as _decline_rows_en but for non-en languages."""
    b_lo = report._ym_key(*BASELINE_START)
    b_hi = report._ym_key(*BASELINE_END)
    r_lo = report._ym_key(*RECENT_START)
    r_hi = report._ym_key(*RECENT_END)
    query = f"""
        SELECT
            qid,
            COUNT(CASE WHEN (year*100+month) BETWEEN {b_lo} AND {b_hi}
                       THEN 1 END) AS baseline_months,
            COUNT(CASE WHEN (year*100+month) BETWEEN {r_lo} AND {r_hi}
                       THEN 1 END) AS recent_months,
            COALESCE(SUM(CASE WHEN (year*100+month) BETWEEN {b_lo} AND {b_hi}
                              THEN views END), 0) AS baseline_sum,
            COALESCE(SUM(CASE WHEN (year*100+month) BETWEEN {r_lo} AND {r_hi}
                              THEN views END), 0) AS recent_sum
        FROM cross_lang_monthly_views
        WHERE language = ?
        GROUP BY qid
    """
    out: list[tuple[str, float, float]] = []
    with vital_db.get_connection() as conn:
        for r in conn.execute(query, (language,)).fetchall():
            if (r["baseline_months"] != BASELINE_MONTHS_EXPECTED
                    or r["recent_months"] != RECENT_MONTHS_EXPECTED
                    or r["baseline_sum"] == 0):
                continue
            out.append((
                r["qid"],
                r["baseline_sum"] / BASELINE_MONTHS_EXPECTED,
                r["recent_sum"] / RECENT_MONTHS_EXPECTED,
            ))
    return out


def _summarize(rows: list[tuple[str, float, float]]) -> dict:
    pct = [(rp - bp) * 100.0 / bp for (_, bp, rp) in rows]
    if not pct:
        return {"n": 0, "median": None, "p25": None, "p75": None, "view_wt": None}
    s = sorted(pct)
    q1, _, q3 = statistics.quantiles(s, n=4, method="inclusive")
    base_total = sum(bp for (_, bp, _) in rows)
    recent_total = sum(rp for (_, _, rp) in rows)
    return {
        "n": len(rows),
        "median": statistics.median(s),
        "p25": q1,
        "p75": q3,
        "view_wt": (recent_total - base_total) * 100.0 / base_total if base_total else None,
    }


def _aggregate_monthly(language: str) -> dict[tuple[int, int], int]:
    """Return {(year, month): total_views} for full-coverage QIDs in a language.

    Uses the same coverage filter as _decline_rows_*: articles with complete
    baseline+recent windows and non-zero baseline.
    """
    if language == "en":
        rows = _decline_rows_en()
        qids = {q for (q, _, _) in rows}
        if not qids:
            return {}
        placeholders = ",".join("?" for _ in qids)
        query = f"""
            SELECT mv.year, mv.month, SUM(mv.views) AS total
            FROM monthly_views mv
            JOIN samples s ON s.title = mv.title
            WHERE s.wikidata_id IN ({placeholders})
            GROUP BY mv.year, mv.month
            ORDER BY mv.year, mv.month
        """
        with vital_db.get_connection() as conn:
            return {(r["year"], r["month"]): r["total"]
                    for r in conn.execute(query, list(qids)).fetchall()}
    rows = _decline_rows_lang(language)
    qids = {q for (q, _, _) in rows}
    if not qids:
        return {}
    placeholders = ",".join("?" for _ in qids)
    query = f"""
        SELECT year, month, SUM(views) AS total
        FROM cross_lang_monthly_views
        WHERE language = ? AND qid IN ({placeholders})
        GROUP BY year, month
        ORDER BY year, month
    """
    with vital_db.get_connection() as conn:
        return {(r["year"], r["month"]): r["total"]
                for r in conn.execute(query, [language, *qids]).fetchall()}


def chart_cross_lang_bars(
    summaries: list[tuple[str, dict]],
    path: Path = OUTPUT_DIR / "03_cross_lang_decline.png",
) -> None:
    """Horizontal bars, one per language, sorted worst-first on median decline."""
    summaries = [(lang, s) for (lang, s) in summaries if s["n"] > 0]
    summaries.sort(key=lambda ls: ls[1]["median"])

    labels = [f"{LANGUAGE_LABELS.get(lang, lang)} ({lang})" for (lang, _) in summaries]
    medians = [s["median"] for (_, s) in summaries]
    p25 = [s["p25"] for (_, s) in summaries]
    p75 = [s["p75"] for (_, s) in summaries]

    fig, ax = plt.subplots(figsize=(9, 0.45 * len(summaries) + 2.5))
    y = list(range(len(summaries)))

    colors = [LANGUAGE_COLORS.get(lang, "#444444") for (lang, _) in summaries]
    ax.barh(y, medians, color=colors, alpha=0.78, height=0.7, zorder=3)

    ax.errorbar(
        medians, y,
        xerr=[[m - lo for m, lo in zip(medians, p25)],
              [hi - m for m, hi in zip(medians, p75)]],
        fmt="none", ecolor="#444444", elinewidth=1.0, capsize=3, zorder=4,
    )

    ax.axvline(0, color="#555555", linewidth=0.8, zorder=2)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))
    ax.set_xlabel("Change in per-month-average pageviews (median across articles)")

    xmin, xmax = ax.get_xlim()
    annot_x = xmax + (xmax - xmin) * 0.02
    for yi, (_, s) in enumerate(summaries):
        ann = f"n={s['n']:,}   view-wt {s['view_wt']:+.1f}%"
        ax.text(annot_x, yi, ann, va="center", fontsize=8.5, color="#333333")
    ax.set_xlim(xmin, xmax + (xmax - xmin) * 0.30)

    fig.suptitle(
        "Vital Articles decline by Wikipedia language",
        fontsize=13, fontweight="bold", y=0.995,
    )
    ax.set_title(
        "Bars: median per-article % change (2016–19 vs 2025-04 through 2026-03).  "
        "Whiskers: p25–p75.",
        fontsize=9.5, color="#444444", pad=6,
    )
    fig.text(
        0.5, 0.01,
        "Same Wikidata items across languages, but each language's decline is "
        "measured against its own 2016–19 baseline.",
        ha="center", fontsize=7.5, color="#666666",
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    print(f"Wrote {path}")


def chart_cross_lang_trajectories(
    languages: list[str],
    path: Path = OUTPUT_DIR / "04_cross_lang_trajectories.png",
) -> None:
    """One LOESS-smoothed line per language, indexed to 100 at the baseline mean."""
    fig, ax = plt.subplots(figsize=(10, 5.5))
    _shade_comparison_windows(ax, label=True)

    for lang in languages:
        totals = _aggregate_monthly(lang)
        if not totals:
            continue
        keys = sorted(totals.keys())
        xs = [_ym_to_float(y, m) for (y, m) in keys]
        ys_raw = [totals[k] for k in keys]

        baseline_mean = statistics.mean(
            v for (y, m), v in totals.items()
            if (y, m) >= BASELINE_START and (y, m) <= BASELINE_END
        )
        if baseline_mean == 0:
            continue
        ys_idx = [v * 100.0 / baseline_mean for v in ys_raw]
        smoothed = list(lowess(ys_idx, xs, frac=LOESS_SPAN, return_sorted=False))

        color = LANGUAGE_COLORS.get(lang, "#444444")
        label = f"{LANGUAGE_LABELS.get(lang, lang)} ({lang})"
        ax.plot(xs, smoothed, color=color, linewidth=2.0, label=label, zorder=3)

    covid_x = _ym_to_float(*COVID_PEAK_YM)
    chatgpt_x = _ym_to_float(*CHATGPT_LAUNCH_YM)
    ax.axvline(covid_x, color="#555555", linestyle=":", linewidth=1.0, alpha=0.6)
    ax.axvline(chatgpt_x, color=RECENT_BAND_COLOR, linestyle="--", linewidth=1.0, alpha=0.65)
    ymin, ymax = ax.get_ylim()
    ax.text(covid_x + 0.05, ymax - (ymax - ymin) * 0.03, "COVID",
            fontsize=8, color="#555555", va="top")
    ax.text(chatgpt_x + 0.05, ymax - (ymax - ymin) * 0.03, "ChatGPT",
            fontsize=8, color=RECENT_BAND_COLOR, va="top")

    ax.axhline(100, color="#999999", linewidth=0.7, zorder=1)
    ax.set_xlabel("Year")
    ax.set_ylabel("Monthly pageviews (index: 2016–19 mean = 100)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}"))
    ax.legend(loc="lower left", fontsize=8, frameon=False, ncol=2)

    fig.suptitle(
        "Cross-language aggregate pageviews (LOESS, indexed to baseline)",
        fontsize=13, fontweight="bold", y=0.995,
    )
    ax.set_title(
        "Each line: sum of monthly pageviews across the same Wikidata items "
        "in that language, normalized to its own 2016–19 mean.",
        fontsize=9.5, color="#444444", pad=6,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    print(f"Wrote {path}")


def print_summary_table(summaries: list[tuple[str, dict]]) -> None:
    print()
    print(f"{'language':<12} {'n':>6} {'median':>9} {'p25':>9} {'p75':>9} {'view-wt':>10}")
    print("-" * 60)
    for lang, s in sorted(summaries, key=lambda ls: ls[1]["median"] if ls[1]["n"] else 0):
        if s["n"] == 0:
            continue
        print(f"{lang:<12} {s['n']:>6,} {s['median']:>8.1f}% "
              f"{s['p25']:>8.1f}% {s['p75']:>8.1f}% {s['view_wt']:>9.1f}%")
    print()


def main() -> int:
    setup_style()
    languages_non_en = _available_non_en_languages()
    all_languages = ["en"] + languages_non_en

    summaries: list[tuple[str, dict]] = []
    for lang in all_languages:
        rows = _decline_rows_en() if lang == "en" else _decline_rows_lang(lang)
        summaries.append((lang, _summarize(rows)))

    print_summary_table(summaries)

    chart_cross_lang_bars(summaries)
    chart_cross_lang_trajectories(all_languages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
