"""Multivariate OLS: which article-level features predict en pageview decline?

Dependent variable: pct_change in per-month-average pageviews (2016-19 vs
2025-04..2026-03), restricted to the 4,826 full-coverage articles.

Independent variables (all en.wikipedia):
  - expected_quality      : Lift Wing articlequality expected score (0=Stub..5=FA)
  - log_revisions         : log1p(total lifetime edits) — XTools
  - log_editors           : log1p(unique lifetime editors) — XTools
  - anon_ratio            : anon_edits / revisions — IP-editor share
  - minor_ratio           : minor_edits / revisions — minor-edit share
  - log_watchers          : log1p(watchlist subscribers)
  - article_age_years     : years since article creation
  - log_days_since_edit   : log1p(days since most recent edit)
  - primary_topic         : one-hot encoded editorial bucket

Reports: (1) baseline R² with topic only, (2) R² with each variable added,
(3) full-model coefficients with standardized effect sizes and p-values.

Usage:
    uv sync --extra dev --extra analysis
    uv run python analysis/vital-articles/multivariate_report.py
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent))

import report  # noqa: E402
import vital_db  # noqa: E402


class OLSFit:
    """Minimal OLS: coefficients, standard errors, p-values, R².

    Avoids the statsmodels.formula.api dep (currently broken against
    scipy>=1.16 on this box). Returns topic-expanded dummies with a
    reference category dropped, matching what smf.ols would produce.
    """
    def __init__(self, y: np.ndarray, X: np.ndarray, names: list[str]):
        self.y = y
        self.X = X
        self.names = names
        self.n, self.k = X.shape
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        self.coef = coef
        resid = y - X @ coef
        dof = self.n - self.k
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
        self.sigma2 = sigma2
        self.dof = dof

    def param(self, name: str) -> float | None:
        if name in self.names:
            return self.coef[self.names.index(name)]
        return None

    def pvalue(self, name: str) -> float | None:
        if name in self.names:
            return float(self.p[self.names.index(name)])
        return None


def build_design(df: pd.DataFrame, continuous: list[str]) -> tuple[np.ndarray, list[str]]:
    """Build an OLS design matrix: intercept + one-hot topic (reference=first
    alphabetically) + continuous predictors.
    """
    topics = sorted(df["primary_topic"].unique())
    ref = topics[0]
    dummies = [(t, (df["primary_topic"] == t).astype(float).values) for t in topics if t != ref]
    cols = [np.ones(len(df))]
    names = ["Intercept"]
    for t, col in dummies:
        cols.append(col)
        names.append(f"topic[{t}]")
    for c in continuous:
        cols.append(df[c].astype(float).values)
        names.append(c)
    X = np.column_stack(cols)
    return X, names


MIN_BASELINE = 100   # filter out sub-100/mo baselines (very noisy pct_change)
MIN_RECENT = 1       # guard against zero-recent log issues

def load_frame() -> pd.DataFrame:
    """Assemble a per-article dataframe keyed on title, en-only.

    Dependent variable is log10((recent+1)/(baseline+1)), which:
      - is symmetric (a halving looks as big as a doubling),
      - is well-behaved for OLS even at the tails,
      - avoids the huge right-skew that pct_change has when baselines are tiny
        (e.g. Pokémon Red, Blue, and Yellow: 3/mo → 39k/mo, pct_change = 1.3M%).

    Also filters out articles with baseline < MIN_BASELINE/mo, where pct_change
    is dominated by measurement noise rather than signal.
    """
    decline_rows, _ = report.compute_decline_rows()
    d = pd.DataFrame(decline_rows)  # title, primary_topic, baseline_per_month, recent_per_month, pct_change
    d = d[d["baseline_per_month"] >= MIN_BASELINE].copy()
    d["log_ratio"] = np.log10((d["recent_per_month"] + 1) / (d["baseline_per_month"] + 1))
    d = d[["title", "primary_topic", "baseline_per_month", "pct_change", "log_ratio"]]

    with vital_db.get_connection() as conn:
        q = pd.read_sql_query(
            "SELECT title, expected_quality, predicted_class "
            "FROM article_quality WHERE status = 'ok'",
            conn,
        )
        fresh = pd.read_sql_query(
            "SELECT s.title, af.rev_timestamp "
            "FROM article_freshness af "
            "JOIN samples s ON s.wikidata_id = af.qid "
            "WHERE af.language = 'en' AND af.status = 'ok'",
            conn,
        )
        stats = pd.read_sql_query(
            "SELECT s.title, a.revisions, a.editors, a.anon_edits, "
            "       a.minor_edits, a.watchers, a.created_at "
            "FROM article_stats a "
            "JOIN samples s ON s.wikidata_id = a.qid "
            "WHERE a.language = 'en' AND a.status = 'ok'",
            conn,
        )

    df = d.merge(q, on="title", how="inner") \
          .merge(fresh, on="title", how="inner") \
          .merge(stats, on="title", how="inner")

    # Derived features.
    now = datetime.now(tz=timezone.utc)
    df["days_since_edit"] = df["rev_timestamp"].apply(
        lambda s: (now - datetime.fromisoformat(s.replace("Z", "+00:00"))).days
    )
    df["article_age_years"] = df["created_at"].apply(
        lambda s: (now - datetime.fromisoformat(s.replace("Z", "+00:00"))).days / 365.25
    )
    df["log_revisions"] = np.log1p(df["revisions"])
    df["log_editors"] = np.log1p(df["editors"])
    df["log_watchers"] = np.log1p(df["watchers"])
    df["log_days_since_edit"] = np.log1p(df["days_since_edit"])
    df["anon_ratio"] = df["anon_edits"] / df["revisions"].replace(0, np.nan)
    df["minor_ratio"] = df["minor_edits"] / df["revisions"].replace(0, np.nan)

    # Drop rows missing critical fields.
    critical = [
        "log_ratio", "expected_quality", "log_revisions", "log_editors",
        "log_watchers", "log_days_since_edit", "anon_ratio", "minor_ratio",
        "article_age_years", "primary_topic",
    ]
    return df.dropna(subset=critical).reset_index(drop=True)


def _sig(p: float) -> str:
    if p < 0.001:
        return " ***"
    if p < 0.01:
        return "  **"
    if p < 0.05:
        return "   *"
    return ""


def fit_models(df: pd.DataFrame) -> None:
    print("=" * 80)
    print(f"Multivariate OLS: English pageview decline (n = {len(df):,} articles)")
    print(f"Dependent: log10((recent+1)/(baseline+1))  "
          f"[0 = no change, -0.3 ≈ halved, +0.3 ≈ doubled]")
    print(f"Filter: baseline ≥ {MIN_BASELINE}/mo (noisy tiny-baseline rows dropped)")
    print("=" * 80)

    y = df["log_ratio"].astype(float).values

    # (1) topic-only baseline
    X_base, names_base = build_design(df, continuous=[])
    base = OLSFit(y, X_base, names_base)
    print(f"\n(1) Topic-only baseline        R²  = {base.r2:.4f}  "
          f"adj R² = {base.r2_adj:.4f}")

    # (2) incremental: add one variable at a time
    variables = [
        "expected_quality",
        "log_revisions",
        "log_editors",
        "log_watchers",
        "log_days_since_edit",
        "anon_ratio",
        "minor_ratio",
        "article_age_years",
    ]
    prev_r2 = base.r2_adj
    running: list[str] = []
    print(f"\n(2) Incremental fit (each line adds one variable to the previous model):")
    print(f"    {'variable added':<24} {'R²':>8} {'adj R²':>8} {'ΔR²':>8}")
    print(f"    {'(topic only, baseline)':<24} {base.r2:>8.4f} {base.r2_adj:>8.4f} {'-':>8}")
    for var in variables:
        running.append(var)
        X, names = build_design(df, continuous=running)
        m = OLSFit(y, X, names)
        dr = m.r2_adj - prev_r2
        print(f"    + {var:<22} {m.r2:>8.4f} {m.r2_adj:>8.4f} {dr:>+8.4f}")
        prev_r2 = m.r2_adj
    full = m

    # (3) full-model coefficients with standardized betas
    print(f"\n(3) Full-model coefficients (all variables, topic-adjusted):")
    print(f"    full model R² = {full.r2:.4f}  adj R² = {full.r2_adj:.4f}  "
          f"n = {len(df):,}")
    print()
    print(f"    {'variable':<24} {'β (raw)':>14} {'std β':>10} {'p-value':>12}")
    print("    " + "-" * 62)
    sy = df["log_ratio"].std()
    for name in ["Intercept"] + variables:
        beta = full.param(name)
        pval = full.pvalue(name)
        if beta is None:
            continue
        if name in df.columns:
            sx = df[name].std()
            std_b = beta * sx / sy
            std_str = f"{std_b:+.3f}"
        else:
            std_str = "   (n/a)"
        print(f"    {name:<24} {beta:>+14.4f} {std_str:>10} {pval:>12.4g}{_sig(pval)}")

    print("\n    Standardized β = effect size when the predictor moves 1 SD.")
    print("    Example: a std β of +0.10 means a 1-SD increase in that variable")
    print("    is associated with a 0.10-SD increase in pct_change (= less loss).")
    print("    Positive std β → more of that variable → LESS pageview loss.")
    print("    Negative std β → more of that variable → MORE pageview loss.")

    # (4) topic coefficients
    print(f"\n(4) Topic coefficients in the full model (relative to '{sorted(df['primary_topic'].unique())[0]}'):")
    topic_rows = [(name, full.param(name), full.pvalue(name))
                  for name in full.names if name.startswith("topic[")]
    for name, beta, pval in sorted(topic_rows, key=lambda r: r[1] or 0):
        short = name.replace("topic[", "").rstrip("]")
        print(f"    {short:<32} {beta:>+9.2f}  (p={pval:.3g}){_sig(pval)}")


def main() -> int:
    df = load_frame()
    if df.empty:
        print("Nothing to regress — are all the input tables populated?")
        return 1
    fit_models(df)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
