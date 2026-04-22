# Vital Articles cross-language analysis — current findings

Last updated: 2026-04-22. Purpose of this doc: snapshot the state of the analysis
in prose + numbers so a fresh Claude session can pick up without re-running the
entire pipeline. Reload this file first, read top-level `CLAUDE.md` second,
then you have the full context.

## Sampling frame and hypothesis

- **Sample**: 5,000 of 39,707 Wikipedia Level-5 Vital Articles, stratified by the
  11 editorial topic buckets (Arts, Biology and health sciences, Everyday life,
  Geography, History, Mathematics, People, Philosophy and religion, Physical
  sciences, Society and social sciences, Technology). Seed 20260421, pinned.
- **Baseline window**: 2016-01..2019-12 (48 months), the pre-COVID plateau.
- **Recent window**: 2025-04..2026-03 (12 months), the most recent year.
- **Dependent variable**: per-article percent change between windows, and
  `log10((recent+1)/(baseline+1))` for regressions (symmetric, robust to tiny
  baselines). Per-article analyses typically filter to baseline ≥ 100 views/mo
  to exclude articles where pct_change is measurement noise.
- **Hypothesis**: LLM substitution (primarily ChatGPT) is a major driver of
  Wikipedia's recent pageview decline. Tested by (a) topic-level decline
  ordering (reference-like content should fall hardest, narrative-rich content
  should hold up) and (b) language-level decline ordering (markets with more
  LLM access should decline more).

## 12 viable languages (all complete as of 2026-04-22)

Per-language data coverage is ~99% for every language except fa (~87%, due to
sitelink-title drift on smaller wiki). Fetched and stored: pageviews (en + 11
non-en), freshness (days since last edit), language-agnostic quality score,
XTools articleinfo stats, per-language Lift Wing quality class probabilities
(for the 6 languages where those models exist).

## Headline numbers — aggregate decline 2016-19 vs 2025-04..2026-03

View-weighted per-article (= aggregate monthly-views change), sorted best to
worst for the 9 languages we've focused on (excluding zh, fa, uk for this cut):

| Lang | Baseline/mo | Recent/mo | % change |
|---|---|---|---|
| en | 97.07M | 82.71M | −14.8% |
| ru | 13.20M | 10.01M | −24.1% |
| fr | 10.82M |  8.04M | −25.7% |
| it |  7.84M |  5.47M | −30.2% |
| ja | 10.95M |  7.04M | −35.7% |
| de | 15.31M |  9.29M | −39.3% |
| ar |  2.46M |  1.39M | −43.4% |
| pt |  5.75M |  2.76M | −52.0% |
| es | 19.84M |  8.79M | −55.7% |
| **TOTAL** | **183.23M** | **135.51M** | **−26.0%** |

Annualized loss across those 9 wikis: **~573M pageviews/year** of sampled-Vital-Articles
traffic, gone. The sample is ~12.5% of all Level-5 Vital Articles (5,000 / 39,707),
so Vital-Articles-wide the drop is roughly 8× this, and Wikipedia-wide is larger
still. Treat these as a conservative sampling slice.

For completeness — the three languages set aside for confounds:
- **zh**: median per-article +8.1% but view-weighted −12.0%. Aggregate ~-31% from 2016-19
  baseline. The median is misleading because many long-tail zh articles grew from tiny
  bases while heavy-traffic articles shrank. Use view-weighted for zh.
- **fa**: median +9.0%, view-wt +3.1%. Legitimately near-flat; ChatGPT geo-restricted by
  OpenAI sanctions for Iran, plus Persian LLM quality lags.
- **uk**: median +26.2%, view-wt −2.5%. War-driven traffic pattern inflates baseline-to-recent
  comparison; post-2022 attention surge distorts peak-to-trough math (its "trough" is
  2016 pre-war). Not a clean test case.

## English-only multivariate (n=4,252, full adj R² = 0.174)

With topic dummies + article-level features, predicting log_ratio:

| Feature | std β | p |
|---|---|---|
| log_editors | +0.52 | <.001 |
| log_revisions | −0.46 | <.001 |
| anon_ratio | −0.28 | <.001 |
| log_days_since_edit (staleness) | −0.15 | <.001 |
| log_watchers | −0.10 | <.001 |
| minor_ratio | −0.09 | <.001 |
| score (Lift Wing quality) | +0.07 | <.001 |
| article_age_years | −0.02 | n.s. |

Topic adj R² baseline alone = 0.113. Full model adds +0.060 adj R². Biggest
incremental contributors beyond topic: freshness (+0.023), anon_ratio (+0.019).

The log_revisions / log_editors sign-flip is a multivariate finding: after
controlling for unique editors, more total revisions *per editor* predicts more
decline — plausibly an edit-churn / bot-maintenance effect rather than healthy
maintenance.

Quality correlation is small and partially topic-confounded: STEM articles are
both lower-quality and more LLM-substitutable, so raw quality-decline correlation
mostly reflects topic, not quality-as-resilience. Only +0.07 std β survives
topic adjustment.

## Per-language multivariate (10 languages with n ≥ 100)

| Lang | n | ΔR² from features | Quality | Staleness | log_rev | log_ed |
|---|---|---|---|---|---|---|
| en | 4,252 | +0.062 | +0.07*** | −0.15*** | −0.46*** | +0.52*** |
| es |   892 | +0.133 | +0.16*** | −0.09 ** | −0.38  * | +0.19   |
| fr | 1,189 | +0.052 | +0.12 ** | −0.15*** |   −0.19  | +0.12   |
| de | 1,389 | +0.058 | +0.11*** | −0.12*** | −0.26  * | +0.27  *|
| zh |   446 | +0.039 |   +0.10  | −0.19*** |   +0.39  | −0.49  *|
| ru |   973 | +0.105 | +0.13 ** | −0.19*** | −0.37  * | +0.44 **|
| it |   193 | +0.072 |   +0.09  |   −0.13  |   −0.37  | +0.46   |
| ar |   184 | +0.066 |   +0.15  |   −0.04  |   −0.26  | +0.20   |
| pt |   330 | +0.168 | +0.14  * | −0.16 ** |   −0.06  | +0.09   |
| ja | 1,024 | +0.035 | +0.12 ** |   −0.06  |   −0.12  | +0.15   |

(fa n=79 and uk n=15 too small for per-language regression.)

Per-language n is much smaller than total sample because the baseline ≥ 100
filter drops low-traffic articles on each wiki. Smaller wikis have fewer
high-baseline articles to regress on.

## Pooled regression (n=10,966 across 11 languages, topic + language fixed effects)

adj R² full = 0.301, baseline (topic + lang FE only) = 0.243.

Standardized coefficients, all p<.001 except article_age_years (n.s.):
- **quality score**: +0.11 (higher quality → less loss)
- **log_revisions**: −0.31 (more edits → more loss, after controlling for editors)
- **log_editors**: +0.29 (more unique editors → less loss)
- **log_days_since_edit**: −0.14 (staler → more loss)
- **anon_ratio**: −0.25 (more IP-editor share → more loss)
- **minor_ratio**: −0.06
- **log_watchers**: −0.05
- **article_age_years**: ~0 (n.s.)

### Language fixed-effect offsets vs en (log_ratio units, negative = more loss)

| Lang | Offset vs en | Significance |
|---|---|---|
| pt | −0.27 | *** |
| es | −0.27 | *** |
| ar | −0.17 | *** |
| it | −0.17 | *** |
| de | −0.13 | *** |
| fr | −0.11 | *** |
| ja | −0.09 | *** |
| ru | −0.04 | *** |
| uk | −0.03 | n.s. |
| zh | +0.01 | n.s. |
| **fa** | +0.10 | ** |

Interpretation: even after controlling for all article-level features (topic,
maintenance, quality), pt/es/ar decline ~0.17–0.27 log-units more than en.
zh and uk track en. fa significantly outperforms. Whatever drives the
big-decline languages is a wiki-/market-level effect, not article-level.

## LLM penetration data (heavily caveated — see metric-type audit in session history)

Independent per-country population-share data for ChatGPT adoption is sparse.
The only language with a clean A-category (population share) estimate is
Japanese (GMO study: 42.5% adoption in Japan, Feb 2025). Most "market share"
figures cited in commercial sources (SimilarWeb, Visual Capitalist, etc.) are
either:
- **traffic share (D)** — sum of user count × engagement; confounds "lots of
  users" with "heavy users."
- **user share among AI users (C)** — competitive position in an AI market, not
  penetration of AI into the total population.
- **survey adoption (E)** — skewed toward online/educated respondents.

The cross-language Wikipedia-decline data is probably more reliable as primary
evidence of LLM impact than any of those secondary sources for most markets.
Japan at −35.7% view-weighted is compatible with 42.5% population adoption;
but this is one data point, not a clean validation.

ChatGPT availability status is more reliable:
- **Direct access** (available everywhere in market): en, es, fr, de, it, pt, ja, ar, uk
- **Blocked**: zh (Great Firewall)
- **Restricted**: ru (blocked but VPN-accessible, ~26% share vs DeepSeek 20%/YandexGPT 12%
  per Oct 2025 Statcounter), fa (OpenAI sanctions; VPN-accessible)

## Key visualizations

Rendered PNGs in `output/charts/`:
- `01_bucket_decline.png` — English-only per-bucket decline bar chart
- `02_aggregate_traffic.png` — English-only aggregate trajectory
- `03_cross_lang_decline.png` — Bar chart, one per language, sorted
- `04_cross_lang_trajectories.png` — LOESS lines, indexed to each lang's 2016-19 mean
- `05_freshness_vs_decline.png` — Scatter: median freshness vs median decline per language
- `06_decline_by_availability.png` — Same as 03 but colored by ChatGPT availability
- `07_cross_lang_trajectories_jan2020.png` — LOESS lines, all pegged to Jan 2020 = 100. **This is the clearest visual.**

Full multivariate regression output is saved at `/tmp/vital_multivariate.txt`
(also reproduce via `uv run python analysis/vital-articles/multivariate_cross_lang.py`).

## Reference article sets

- `output/representative_articles.txt` — 6 articles per bucket at p25 and p75 of
  decline. Useful for picking specific examples for writing.
- Per-article CSV `output/decline_by_bucket.csv` (gitignored, regeneratable from
  `report.py`).

## Known confounds and caveats

- **Spike-driven peaks**: Apr/May 2020 (COVID) and Oct 2023 (news cycle) produce
  aggregate-traffic peaks for many wikis. Peak-to-trough comparisons inflate the
  decline; use the 2016-19 baseline window for sustained-level comparisons.
- **zh median ≠ zh aggregate**: zh's long-tail articles grew from small bases
  while heavy-traffic articles shrank. Report view-weighted, not median.
- **uk is not a clean test case**: war-driven attention spike distorts the
  baseline-to-recent comparison (its pre-war attention level was very low).
- **ru has multiple confounders**: Roskomnadzor pressure, state-backed
  Wikipedia forks (Ruwiki, Runiversalis), post-2022 emigration, VPN-distorted
  geography. Its −24% decline is consistent with LLM substitution plus these
  non-LLM pressures — not a pure test.
- **fa similarly has confounds**: Iran sanctions, VPN use, diaspora/domestic
  measurement mix. Near-flat result is suggestive not clean.
- **Arabic data is Gulf-skewed**: Gulf states are ~10–15% of Arabic speakers
  but dominate the public adoption data from commercial reports. Egypt,
  Maghreb, Iraq, Syria etc. are underrepresented.
- **Multicollinearity**: log_revisions and log_editors are strongly correlated;
  their opposite signs in the regression are a known pattern. Interpret the
  net effect (editors-adjusted revisions) rather than either alone.
- **Topic-quality confound**: STEM articles skew both lower-quality and more
  LLM-substitutable. The quality-decline relationship is partially mediated by
  topic.

## Pending: regression rerun needed to update the per-language tables

Three recent changes to `multivariate_cross_lang.py` are in the repo but have
not yet been run against `vital.db` (the only machine with the populated DB
was offline when the changes landed). Until someone reruns, the per-language
n's and R² numbers in sections above are from the pre-change run and will
shift — mainly upward for small-n languages.

Changes pending rerun:
1. **Whole-year baseline relaxation**: baseline window used to require all 48
   months of 2016-19; now accepts any whole Jan..Dec year(s) in 2016-19 that
   the article has all 12 months on a given wiki, and averages over just the
   kept years. Preserves seasonal cancellation. Biggest n-boost expected on
   uk, fa, ar, pt (younger-on-wiki articles).
2. **`MIN_BASELINE` lowered from 100 → 30**: the old threshold was set for a
   pct_change-based analysis; current dep var is log_ratio which handles
   small baselines cleanly via +1 smoothing. Biggest n-boost on all smaller
   wikis.
3. **Topic-vs-features adj-R² decomposition** added as a new output table:
   `R²(topic only)`, `R²(features only)`, `R²(topic+features)`, plus
   `Unique(topic)`, `Unique(features)`, `Shared`. Lets us say how much
   topic matters relative to the 8 continuous features.

To run (needs the populated DB):
```
uv run --extra dev --extra analysis python analysis/vital-articles/multivariate_cross_lang.py
```

After running, update (in this file):
- The per-language multivariate table (line ~96) with the new n's and coefs
- Add the new R² decomposition table below it
- The "n=10,966" pooled-regression headline if it shifts
- The blog draft at `output/blog_post.md` (tables in §4, §5 and the note at
  the bottom currently say "awaiting one more regression rerun")

If the decomposition shows topic's unique contribution is clearly larger than
features', or vice versa, that's a finding worth promoting into the blog's
§5 narrative. Currently §5 previews the direction but waits for numbers.

## Open questions / possible next-session work

- **Within-language topic×decline interaction**: does the "Technology/Math/Physical
  sciences decline hardest" finding from en replicate in other languages? Cross-
  language topic regression.
- **Editor behavior change**: is the anon-ratio effect driven by articles that
  used to get anonymous contributions and now don't? Time-series on editor
  activity rather than cross-section.
- **Singer et al. (2017) information-need classifier**: NEXT_STEPS.md has this
  parked. Content-based classifier for quick_fact / overview / in_depth as a
  more direct "LLM-substitutability" proxy than editorial topic.
- **Anti-correlation check**: are there articles whose traffic *grew* in every
  viable language since 2019? What do they have in common? (hypothesis: news-
  driven / current-events topics that LLMs can't answer because they don't have
  fresh training data)
- **Cross-language topic fixed effects**: the pooled regression has language
  FE but within-topic slopes of the features. Check if coefficients differ
  significantly by topic.

## What to read first when reloading context

1. This file (`FINDINGS.md`) — current state and numbers.
2. Top-level `CLAUDE.md` — pipeline commands and schema.
3. `analysis/vital-articles/NEXT_STEPS.md` — originally parked follow-ups
   (cross-language QID comparison is now done; Singer classifier still pending).
4. `output/charts/07_cross_lang_trajectories_jan2020.png` — clearest visual
   summary of the cross-language story.
