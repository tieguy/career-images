"""Per-language sitelink coverage of the Vital Articles sample.

For each language Wikipedia, how many of our 5,000 sampled en.wikipedia
Vital Articles (Level 5) have a corresponding article? This is the
viability check before fetching pageviews for another language: if
language X covers <50% of the sample, we can't do an apples-to-apples
decline comparison without heavy caveats.

Thresholds (tunable via flags):
  viable  — ≥80% coverage  (green light for pageview fetch)
  partial — 50% ≤ x < 80%  (usable with coverage caveat)
  sparse  — <50%           (skip — not worth the fetch cost)

Usage:
    uv run python analysis/vital-articles/coverage_report.py
    uv run python analysis/vital-articles/coverage_report.py --top 50
    uv run python analysis/vital-articles/coverage_report.py --viable-threshold 70
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import vital_db  # noqa: E402

DEFAULT_TOP_N = 30
DEFAULT_VIABLE_PCT = 80
DEFAULT_PARTIAL_PCT = 50


def _threshold_label(pct: float, viable: float, partial: float) -> str:
    if pct >= viable:
        return "viable"
    if pct >= partial:
        return "partial"
    return "sparse"


def _load_coverage() -> tuple[int, int, list[tuple[str, int]]]:
    """Returns (total_sampled, qids_resolved, [(language, covered_count), ...])."""
    with vital_db.get_connection() as conn:
        (total_sampled,) = conn.execute("SELECT COUNT(*) FROM samples").fetchone()
        (qids_resolved,) = conn.execute(
            "SELECT COUNT(*) FROM samples WHERE wikidata_id IS NOT NULL"
        ).fetchone()
        rows = conn.execute("""
            SELECT sl.language, COUNT(DISTINCT sl.qid) AS n
            FROM sitelinks sl
            JOIN samples s ON s.wikidata_id = sl.qid
            GROUP BY sl.language
            ORDER BY n DESC, sl.language
        """).fetchall()
    return total_sampled, qids_resolved, [(r["language"], r["n"]) for r in rows]


def _print_header(total: int, resolved: int) -> None:
    print(f"Sample size: {total:,} articles")
    print(f"Resolved to Wikidata QIDs: {resolved:,}  "
          f"({resolved * 100.0 / total:.2f}%)")
    print()


def _fmt_row(rank: int, lang: str, n: int, denom: int,
             viable: float, partial: float) -> str:
    pct = n * 100.0 / denom
    label = _threshold_label(pct, viable, partial)
    return f"  {rank:>3}. {lang:<10} {n:>5,} / {denom:,}  ({pct:>5.1f}%)   [{label}]"


def _highlight(langs: list[str], covered: dict[str, int],
               denom: int, viable: float, partial: float) -> None:
    for lang in langs:
        n = covered.get(lang, 0)
        pct = n * 100.0 / denom if denom else 0
        label = _threshold_label(pct, viable, partial)
        print(f"  {lang:<10} {n:>5,} / {denom:,}  ({pct:>5.1f}%)   [{label}]")


def run(top_n: int, viable_pct: float, partial_pct: float) -> None:
    total, resolved, coverage = _load_coverage()
    if not coverage:
        print("No sitelinks in the database. Run fetch_sitelinks.py first.")
        return

    denom = resolved
    _print_header(total, resolved)

    buckets = {"viable": 0, "partial": 0, "sparse": 0}
    for _, n in coverage:
        pct = n * 100.0 / denom
        buckets[_threshold_label(pct, viable_pct, partial_pct)] += 1

    print(f"Languages by coverage tier (of {len(coverage):,} total languages with ≥1 article):")
    print(f"  viable   (≥{viable_pct:.0f}%):            {buckets['viable']:>3}")
    print(f"  partial  ({partial_pct:.0f}%–{viable_pct:.0f}%):           {buckets['partial']:>3}")
    print(f"  sparse   (<{partial_pct:.0f}%):            {buckets['sparse']:>3}")
    print()

    print(f"Top {top_n} languages by sample coverage:")
    print("  rank  language   covered / resolved  (pct)     [tier]")
    print("  " + "-" * 60)
    for rank, (lang, n) in enumerate(coverage[:top_n], 1):
        print(_fmt_row(rank, lang, n, denom, viable_pct, partial_pct))
    print()

    # Call out commonly-requested comparison wikis even if outside the top N.
    covered = dict(coverage)
    target = ["es", "fr", "de", "ja", "zh", "pt", "ru", "ar", "it", "id"]
    missing = [t for t in target if t not in {lang for lang, _ in coverage[:top_n]}]
    if missing:
        print("Reference wikis of interest (flagged regardless of top-N cutoff):")
        _highlight(missing, covered, denom, viable_pct, partial_pct)
        print()

    viable_langs = [lang for lang, n in coverage if n * 100.0 / denom >= viable_pct]
    partial_langs = [
        lang for lang, n in coverage
        if partial_pct <= n * 100.0 / denom < viable_pct
    ]
    print("Decision list:")
    print(f"  viable (go): {', '.join(viable_langs) if viable_langs else '(none)'}")
    print(f"  partial (caveat): {', '.join(partial_langs) if partial_langs else '(none)'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                        help=f"Show top N languages (default: {DEFAULT_TOP_N})")
    parser.add_argument("--viable-threshold", type=float, default=DEFAULT_VIABLE_PCT,
                        help="Percent coverage for 'viable' tier (default: 80)")
    parser.add_argument("--partial-threshold", type=float, default=DEFAULT_PARTIAL_PCT,
                        help="Percent coverage for 'partial' tier (default: 50)")
    args = parser.parse_args()
    run(args.top, args.viable_threshold, args.partial_threshold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
