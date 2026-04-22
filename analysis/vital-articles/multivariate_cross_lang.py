"""Per-language + pooled cross-language OLS on Vital Articles pageview decline.

Per-language: runs a topic-adjusted regression separately for each language.
Shows whether the same feature set predicts decline in each linguistic market,
and how coefficients shift across markets.

Pooled: stacks all languages into one regression with language dummies as
fixed effects. Lets us report a single "across wikis, here's the within-article
effect of {freshness / quality / maintenance intensity} on decline, controlling
for topic and for language-specific baselines."

Dependent variable: log10((recent+1)/(baseline+1)) — symmetric, well-behaved,
unlike raw pct_change which gets swamped by tiny-baseline outliers.

Features (per article, per language where applicable):
  - score                  : Lift Wing language-agnostic articlequality (0-1)
  - log_revisions          : log1p(XTools total revisions)
  - log_editors            : log1p(XTools unique editors)
  - log_watchers           : log1p(XTools watchers)
  - log_days_since_edit    : log1p(days since last revision)
  - anon_ratio             : XTools anon_edits / revisions
  - minor_ratio            : XTools minor_edits / revisions
  - article_age_years      : years since article creation
  - primary_topic          : editorial bucket (topic is QID-level, so same
                             across languages for a given article)

Usage:
    uv sync --extra dev --extra analysis
    uv run python analysis/vital-articles/multivariate_cross_lang.py
    uv run python analysis/vital-articles/multivariate_cross_lang.py --languages en,fr,de,es
    uv run python analysis/vital-articles/multivariate_cross_lang.py --only-pooled
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent))

import cross_lang_charts  # noqa: E402
import report  # noqa: E402
import vital_db  # noqa: E402

MIN_BASELINE = 100       # exclude tiny-baseline articles to stabilize pct_change
ALL_LANGUAGES = ["en", "es", "fr", "de", "zh", "ru", "it", "ar", "pt", "fa", "ja", "uk"]

FEATURES = [
    "score",
    "log_revisions",
    "log_editors",
    "log_watchers",
    "log_days_since_edit",
    "anon_ratio",
    "minor_ratio",
    "article_age_years",
]


class OLSFit:
    """Minimal OLS: coefficients, SE, p-values, R². Same helper as the
    en-only multivariate_report.py; duplicated here because the bound scipy
    breaks statsmodels.formula.api on this box.
    """
    def __init__(self, y: np.ndarray, X: np.ndarray, names: list[str]):
        self.y = y
        self.X = X
        self.names = names
        self.n, self.k = X.shape
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        self.coef = coef
        resid = y - X @ coef
        dof = max(self.n - self.k, 1)
        sigma2 = (resid @ resid) / dof
        try:
            cov = sigma2 * np.linalg.inv(X.T @ X)
            self.se = np.sqrt(np.diag(cov))
        except np.linalg.LinAlgError:
            self.se = np.full(self.k, np.nan)
        self.t = self.coef / self.se
        self.p = 2 * (1 - stats.t.cdf(np.abs(self.t), df=dof))
        y_mean = y.mean()
        ss_tot = ((y - y_mean) ** 2).sum()
        ss_res = (resid ** 2).sum()
        self.r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
        self.r2_adj = 1 - (1 - self.r2) * (self.n - 1) / dof
        self.dof = dof

    def param(self, name: str) -> float | None:
        if name in self.names:
            return self.coef[self.names.index(name)]
        return None

    def pvalue(self, name: str) -> float | None:
        if name in self.names:
            return float(self.p[self.names.index(name)])
        return None


def _sig(p: float | None) -> str:
    if p is None or math.isnan(p):
        return ""
    if p < 0.001:
        return "***"
    if p < 0.01:
        return " **"
    if p < 0.05:
        return "  *"
    return ""


def _decline_rows_for(language: str) -> list[dict]:
    """Return [{qid, primary_topic, baseline_per_month, recent_per_month, pct_change}].

    For en: pulls from monthly_views (title-keyed) joined through samples.title.
    For non-en: pulls from cross_lang_monthly_views (qid-keyed).
    """
    decline_rows: list[dict] = []
    if language == "en":
        raw, _ = report.compute_decline_rows()
        topic_by_title = {r["title"]: r["primary_topic"] for r in raw}
        # Need qid for the join key — fetch via samples.
        with vital_db.get_connection() as conn:
            qid_by_title = {
                r["title"]: r["wikidata_id"]
                for r in conn.execute(
                    "SELECT title, wikidata_id FROM samples WHERE wikidata_id IS NOT NULL"
                ).fetchall()
            }
        for r in raw:
            qid = qid_by_title.get(r["title"])
            if qid is None:
                continue
            decline_rows.append({
                "qid": qid,
                "primary_topic": r["primary_topic"],
                "baseline_per_month": r["baseline_per_month"],
                "recent_per_month": r["recent_per_month"],
                "pct_change": r["pct_change"],
            })
        return decline_rows

    triples = cross_lang_charts._decline_rows_lang(language)
    if not triples:
        return []
    qids = [q for (q, _, _) in triples]
    placeholders = ",".join("?" for _ in qids)
    with vital_db.get_connection() as conn:
        topic_by_qid = {
            r["wikidata_id"]: r["primary_topic"]
            for r in conn.execute(
                f"SELECT wikidata_id, primary_topic FROM samples "
                f"WHERE wikidata_id IN ({placeholders})",
                qids,
            ).fetchall()
        }
    for qid, bp, rp in triples:
        topic = topic_by_qid.get(qid)
        if topic is None:
            continue
        decline_rows.append({
            "qid": qid,
            "primary_topic": topic,
            "baseline_per_month": bp,
            "recent_per_month": rp,
            "pct_change": (rp - bp) * 100.0 / bp if bp else 0.0,
        })
    return decline_rows


def load_frame(language: str) -> pd.DataFrame:
    """Assemble per-article dataframe for one language."""
    drows = _decline_rows_for(language)
    if not drows:
        return pd.DataFrame()
    d = pd.DataFrame(drows)
    d = d[d["baseline_per_month"] >= MIN_BASELINE].copy()
    d["log_ratio"] = np.log10((d["recent_per_month"] + 1) / (d["baseline_per_month"] + 1))

    with vital_db.get_connection() as conn:
        fresh = pd.read_sql_query(
            "SELECT qid, rev_timestamp FROM article_freshness "
            "WHERE language = ? AND status = 'ok'",
            conn, params=(language,),
        )
        quality = pd.read_sql_query(
            "SELECT qid, score FROM article_quality_score "
            "WHERE language = ? AND status = 'ok'",
            conn, params=(language,),
        )
        stats_df = pd.read_sql_query(
            "SELECT qid, revisions, editors, anon_edits, minor_edits, "
            "       watchers, created_at "
            "FROM article_stats WHERE language = ? AND status = 'ok'",
            conn, params=(language,),
        )

    df = d.merge(fresh, on="qid", how="inner") \
          .merge(quality, on="qid", how="inner") \
          .merge(stats_df, on="qid", how="inner")

    now = datetime.now(tz=timezone.utc)

    def _days_since(s):
        if not isinstance(s, str):
            return None
        try:
            return (now - datetime.fromisoformat(s.replace("Z", "+00:00"))).days
        except ValueError:
            return None

    df["days_since_edit"] = df["rev_timestamp"].apply(_days_since)
    df["article_age_years"] = df["created_at"].apply(
        lambda s: (_days_since(s) or 0) / 365.25 if isinstance(s, str) else None
    )
    df["log_revisions"] = np.log1p(df["revisions"])
    df["log_editors"] = np.log1p(df["editors"])
    df["log_watchers"] = np.log1p(df["watchers"])
    df["log_days_since_edit"] = np.log1p(df["days_since_edit"])
    df["anon_ratio"] = df["anon_edits"] / df["revisions"].replace(0, np.nan)
    df["minor_ratio"] = df["minor_edits"] / df["revisions"].replace(0, np.nan)
    df["language"] = language

    critical = ["log_ratio", *FEATURES, "primary_topic"]
    return df.dropna(subset=critical).reset_index(drop=True)


def build_design(
    df: pd.DataFrame, continuous: list[str], include_language: bool = False,
) -> tuple[np.ndarray, list[str]]:
    """Intercept + topic dummies (Arts ref) + [language dummies (en ref)] + continuous."""
    topics = sorted(df["primary_topic"].unique())
    ref_topic = topics[0]
    cols = [np.ones(len(df))]
    names = ["Intercept"]
    for t in topics:
        if t == ref_topic:
            continue
        cols.append((df["primary_topic"] == t).astype(float).values)
        names.append(f"topic[{t}]")
    if include_language:
        langs = sorted(df["language"].unique())
        ref_lang = "en" if "en" in langs else langs[0]
        for lang in langs:
            if lang == ref_lang:
                continue
            cols.append((df["language"] == lang).astype(float).values)
            names.append(f"lang[{lang}]")
    for c in continuous:
        cols.append(df[c].astype(float).values)
        names.append(c)
    return np.column_stack(cols), names


def run_per_language(languages: list[str]) -> dict[str, dict]:
    """Return {language: {n, r2_base, r2_full, coefs: {name: (beta, std_beta, p)}}}."""
    results: dict[str, dict] = {}
    for lang in languages:
        df = load_frame(lang)
        if df.empty or len(df) < 100:
            results[lang] = {"n": len(df), "skipped": "too few rows"}
            continue
        y = df["log_ratio"].astype(float).values
        X_base, names_base = build_design(df, continuous=[])
        base = OLSFit(y, X_base, names_base)
        X_full, names_full = build_design(df, continuous=FEATURES)
        full = OLSFit(y, X_full, names_full)
        sy = df["log_ratio"].std()
        coefs = {}
        for feat in FEATURES:
            beta = full.param(feat)
            pval = full.pvalue(feat)
            sx = df[feat].std()
            std_b = beta * sx / sy if (beta is not None and sy) else None
            coefs[feat] = (beta, std_b, pval)
        results[lang] = {
            "n": len(df),
            "r2_base": base.r2_adj,
            "r2_full": full.r2_adj,
            "coefs": coefs,
        }
    return results


def run_pooled(languages: list[str]) -> dict:
    """Stack all languages into one regression with language fixed effects."""
    frames = [load_frame(lang) for lang in languages]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return {}
    df = pd.concat(frames, ignore_index=True)
    y = df["log_ratio"].astype(float).values
    X_base, names_base = build_design(df, continuous=[], include_language=True)
    base = OLSFit(y, X_base, names_base)
    X_full, names_full = build_design(df, continuous=FEATURES, include_language=True)
    full = OLSFit(y, X_full, names_full)
    sy = df["log_ratio"].std()
    coefs = {}
    for feat in FEATURES:
        beta = full.param(feat)
        pval = full.pvalue(feat)
        sx = df[feat].std()
        std_b = beta * sx / sy if (beta is not None and sy) else None
        coefs[feat] = (beta, std_b, pval)
    # Language fixed-effect offsets (vs en).
    lang_fx = {}
    for name in full.names:
        if name.startswith("lang["):
            short = name.replace("lang[", "").rstrip("]")
            lang_fx[short] = (full.param(name), full.pvalue(name))
    return {
        "n": len(df),
        "r2_base": base.r2_adj,
        "r2_full": full.r2_adj,
        "coefs": coefs,
        "lang_fx": lang_fx,
    }


def print_per_language(results: dict[str, dict]) -> None:
    print("=" * 100)
    print("Per-language OLS (topic-adjusted, same feature set per language)")
    print("Dependent: log10((recent+1)/(baseline+1))  |  Baseline: topic dummies only")
    print("=" * 100)
    print(f"\n{'lang':<5} {'n':>6} {'adjR²-base':>11} {'adjR²-full':>11}  "
          f"{'ΔR²':>7}  {'quality':>10} {'staleness':>11} {'log_rev':>9} {'log_ed':>9}")
    print("-" * 100)
    for lang in ALL_LANGUAGES:
        r = results.get(lang)
        if r is None:
            continue
        if "skipped" in r:
            print(f"{lang:<5} {r.get('n', 0):>6}  {r['skipped']}")
            continue
        base = r["r2_base"]
        full = r["r2_full"]
        coefs = r["coefs"]
        def fmt(name):
            tup = coefs.get(name)
            if not tup:
                return "-"
            beta, std_b, pval = tup
            s = _sig(pval)
            return f"{std_b:+.2f}{s}"
        print(f"{lang:<5} {r['n']:>6,} {base:>11.3f} {full:>11.3f}  "
              f"{(full - base):>+7.3f}  "
              f"{fmt('score'):>10} {fmt('log_days_since_edit'):>11} "
              f"{fmt('log_revisions'):>9} {fmt('log_editors'):>9}")
    print("\n  (cells show standardized β with significance: * p<.05, ** p<.01, *** p<.001)")
    print("  positive std β = feature increase → less decline; negative = feature increase → more decline")


def print_pooled(res: dict) -> None:
    if not res:
        print("\nPooled regression: no data")
        return
    print("\n" + "=" * 100)
    print(f"Pooled regression (all languages, topic + language fixed effects)")
    print(f"n = {res['n']:,}  adj R² (topic+lang baseline) = {res['r2_base']:.3f}  "
          f"adj R² (full) = {res['r2_full']:.3f}")
    print("=" * 100)
    print(f"\n{'feature':<24} {'β (raw)':>12} {'std β':>10} {'p-value':>12}")
    print("-" * 62)
    for feat in FEATURES:
        tup = res["coefs"].get(feat)
        if not tup:
            continue
        beta, std_b, pval = tup
        print(f"{feat:<24} {beta:>+12.4f} {std_b:>+10.3f} {pval:>12.4g}{_sig(pval)}")

    print(f"\nLanguage fixed-effect offsets vs en (log_ratio scale; more negative = more loss):")
    for lang, (beta, pval) in sorted(res["lang_fx"].items(), key=lambda x: x[1][0] or 0):
        print(f"  {lang:<5} {beta:>+8.3f}  (p={pval:.3g}){_sig(pval)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--languages", help="Comma-separated subset (default: all 12)")
    parser.add_argument("--only-pooled", action="store_true")
    parser.add_argument("--only-per-language", action="store_true")
    args = parser.parse_args()

    languages = args.languages.split(",") if args.languages else ALL_LANGUAGES

    if not args.only_pooled:
        results = run_per_language(languages)
        print_per_language(results)

    if not args.only_per_language:
        res = run_pooled(languages)
        print_pooled(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
