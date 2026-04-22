# Wikipedia's decline is bigger than careers — and the pattern across languages is interesting

*Follow-up to ["Career articles on Wikipedia: some scary numbers"](../../career-cliff/output/blog_post.md). Draft / outline.*

TLDR (placeholder): The career-article decline wasn't unique to careers. Level-5 Vital Articles — Wikipedia editors' own curated list of ~40k core topics — show the same pattern across 12 languages, and the cross-language differences line up suspiciously well with LLM availability.

---

## 1. Why a follow-up, and what data set this time

The [first post](../../career-cliff/output/blog_post.md) looked at ~4,000 career articles on English Wikipedia. Fair critique: careers are exactly the domain where a chatbot is the obvious substitute ("what does a lawyer do?"), and English Wikipedia is one market. Maybe the decline was a careers-specific or en-specific story.

To test that, I switched sampling frames to **[Wikipedia's Level-5 Vital Articles list](https://en.wikipedia.org/wiki/Wikipedia:Vital_articles/Level/5)** — 39,707 articles that Wikipedia editors themselves curate as the core topics an encyclopedia should cover, stratified across 11 topic buckets (Arts, Biology, Geography, History, Mathematics, People, Philosophy and religion, Physical sciences, Society, Technology, Everyday life). Then sampled 5,000 of those with stratification across the buckets, and followed each article across every language Wikipedia where it had a sitelink.

That lets me ask two questions the first post couldn't:

1. **Is the decline really careers-specific, or does it hit "core knowledge" more broadly?**
2. **Does it look different across languages, and if so — in ways that tell us something about what's actually driving it?**

## 2. What I collected

(This section is more for the record than the reader; feel free to skim.)

Everything is in a local SQLite DB (`vital.db`). The fetcher scripts all live in `analysis/vital-articles/` in the open-source repo.

**Sample frame.** Pulled the on-wiki JSON for every letter of the Level-5 Vital Articles list (Wikipedia keeps these in structured files like `Wikipedia:Vital_articles/data/A.json`). That gave 39,707 article titles tagged by topic bucket. Sampled 5,000 with a pinned random seed, stratified across the 11 buckets.

**Per-article, per-language data for the 5,000 sample:**
- **Sitelinks** — every language Wikipedia where the article exists (via Wikidata's sitelink table). Got me a matrix of (QID × language).
- **Monthly pageviews, 2016-01 through 2026-03** for English, plus the 11 other languages that clear an 80% sitelink-coverage bar for the sample: es, fr, de, zh, ru, it, ar, pt, fa, ja, uk. ~10 years × 12 months × thousands of articles × 12 languages is a lot of points.
- **Article freshness** — the timestamp of the most recent edit on each (article, language) pair, as a staleness proxy.
- **Article quality** — Wikipedia has ML models (Lift Wing, successor to ORES) that score articles Stub→FA. I used the language-agnostic 0–1 model because it returns a comparable score across every wiki. Also pulled per-language Stub..FA class probabilities for the 6 wikis whose own quality model exists.
- **XTools `articleinfo`** — total edits, unique editors, watchers, anon-edit share, minor-edit share, article creation date.

**Convention: synchronous fetching.** An earlier version of the careers project used async concurrent requests and got rate-limited into oblivion by Wikimedia (tens of thousands of false-positive "missing" articles from 429 storms). The vital-articles pipeline is synchronous with a small per-request delay, and finishes in reasonable time.

**Language coverage ended up being:** en/es/fr/de/zh/ru/it/ar/pt/fa/ja/uk. That's a convenience set (the 12 big wikis where ≥80% of the 5k sample has a sitelink), but it spans ChatGPT-direct-access markets (en, es, fr, de, it, pt, ja, ar, uk), a blocked market (zh, behind the Great Firewall), a restricted market (ru, VPN-accessible; DeepSeek and YandexGPT are also big there), and an indirectly-restricted one (fa, sanctions-based restrictions on OpenAI; Persian LLM quality lags).

## 3. Is the decline broader than just careers?

Yes — the decline is not specific to careers, or even to reference-like topics. It shows up across nearly every editorial bucket.

**[PLACEHOLDER graph: `01_bucket_decline.png`]** — Per-bucket decline bar chart for English, 2016-19 baseline vs. 2025-04..2026-03 recent. Expect: Technology, Math, and Physical Sciences fall hardest; Arts and History hold up best.

**[PLACEHOLDER graph: `02_aggregate_traffic.png`]** — Aggregate monthly traffic for the 5k sample on en.wiki, 2016-01..2026-03. Same shape as the career-cliff chart: COVID bump, flat-ish through 2023, sharp step-down starting early 2025.

Headline numbers on English alone: the 5,000-article sample pulled 97.1M views/month at the 2016-19 baseline, and 82.7M/month in the most recent 12 months — a **−14.8%** decline. That's smaller than the careers data set's -28%, which is consistent with "careers are especially LLM-substitutable, but the overall decline is real across core topics."

The topic ordering is also informative:
- Hardest-hit buckets are Technology / Physical sciences / Mathematics — i.e., articles that look like reference lookups ("what is a Fourier transform?").
- Least-hit are Arts, Philosophy and religion, History — narrative or interpretive content.

This matches the chatbot-substitution hypothesis: if a chatbot can give you a definitive answer, the encyclopedia reference lookup is the first thing to go. For narrative or interpretive topics (where there's no single definitive answer) the substitution is weaker.

## 4. The cross-language picture

This is where it gets interesting. The same ~5k sample, across all 12 languages:

**[PLACEHOLDER graph: `03_cross_lang_decline.png` or `06_decline_by_availability.png`]** — bar chart of view-weighted aggregate decline per language, colored by ChatGPT availability. Expect English (-14.8%) on one end, Spanish and Portuguese (-55%, -52%) on the other, with zh/ru/fa as low-decline outliers.

**[PLACEHOLDER graph: `07_cross_lang_trajectories_jan2020.png`]** — LOESS-smoothed trajectories for all 12 languages, indexed so each language's January-2020 mean = 100. This is, in my opinion, the clearest visual of the whole analysis.

Current numbers (view-weighted decline, 2016-19 vs 2025-04..2026-03):

| Language | % change |
|---|---|
| en | −14.8% |
| ru | −24.1% |
| fr | −25.7% |
| it | −30.2% |
| ja | −35.7% |
| de | −39.3% |
| ar | −43.4% |
| pt | −52.0% |
| es | −55.7% |
| **fa** | **+3.1%** |
| **zh** | **−12.0%** |
| **uk** | **−2.5%** |

The bottom three are the languages I set aside as confounded test cases. **zh** has the Great Firewall blocking ChatGPT. **fa** has OpenAI sanctions plus weaker Persian LLM quality. **uk** has a war-driven attention spike that inflates its baseline-to-recent comparison. Notice that the three outliers are exactly the three markets where direct ChatGPT availability is constrained.

The top nine are all direct-ChatGPT-access markets, and their decline ordering roughly tracks what I'd expect from LLM penetration — English declines the least (comparatively), Spanish and Portuguese decline the most.

## 5. Isolating topic from everything else

One honest concern: STEM-heavy topics fall hardest, *and* STEM articles tend to have more bot edits, more anonymous editors, and lower quality scores. Is the topic effect real, or is it really a quality-and-maintenance effect wearing a topic costume?

To answer that I ran a multivariate regression on per-article decline (log10 of recent/baseline) with:

- **Topic** — 11 dummies for editorial bucket
- **Features** — quality score, total edits, unique editors, watchers, freshness (days since last edit), anon-edit ratio, minor-edit ratio, article age

**[PLACEHOLDER decomposition table]** — topic-vs-features adj-R² decomposition per language (Unique-topic / Unique-features / Shared variance). This is produced by `multivariate_cross_lang.py` in the repo; exact numbers pending a rerun.

Preview of the direction from the English-only version:
- Topic alone explains ~11% of per-article variance
- Adding all 8 features on top explains another ~6%
- So topic is the biggest single lever, but features contribute meaningfully on top

And the **language fixed-effect offsets** from the pooled regression (which controls for all article-level factors — same topic, same quality, same freshness, same edit patterns):

| Language | Offset vs en | Interpretation |
|---|---|---|
| pt, es | −0.27 | Decline far beyond what article-level features predict |
| ar, it | −0.17 | Same, smaller magnitude |
| de, fr | −0.11 to −0.13 | Moderate extra decline |
| ja, ru | −0.04 to −0.09 | Close to en |
| uk, zh | ~0 | Indistinguishable from en (confounded) |
| **fa** | **+0.10** | Significantly *less* decline than en |

The key finding: **even after controlling for every article-level feature I could think of, Spanish and Portuguese wikis lose 0.27 log-units more traffic than English, and Persian actually loses less.** That's a market-level effect, not an article-level one. Article-quality and maintenance variables cannot explain the cross-language spread.

## 6. What this tells us (PLACEHOLDER)

(To fill in after the last rerun. Rough intended arc:)

- The careers story was a preview of a much broader pattern. Wikipedia's decline is not limited to LLM-substitutable topics — though those are hit worst.
- The cross-language ordering is *not* explainable by article-level factors like quality, edit velocity, or staleness. Whatever is driving the spread between languages is acting at the market level.
- ChatGPT availability is the most parsimonious explanation of that cross-language ordering I've been able to find: the three outliers in low-decline positions are exactly the three markets with blocked or restricted access, and the high-decline markets are the ones where ChatGPT penetration appears highest.
- But — big caveat — I cannot independently measure LLM penetration per market with any confidence. Commercial "market share" numbers mostly measure traffic share, not population share, and are collected with inconsistent methodology.

## 7. What this *doesn't* tell us (PLACEHOLDER)

(Also to fill in; rough arc:)

- This is observational, not causal. I cannot rule out other 2023-2025 global shifts (search engine changes, social media changes, Wikipedia's own Vector-2022 rollout and UX changes) affecting markets differentially.
- Per-language LLM-penetration data is not independently reliable, so "ChatGPT availability explains the ordering" is qualitative.
- Russia (ru) has multiple confounders beyond LLM substitution (Roskomnadzor pressure, Ruwiki/Runiversalis forks, post-2022 emigration). Its decline is consistent with LLM + these confounders; not a pure test.
- The sample is *Vital Articles*, which skews toward reference-like core content. Wikipedia-wide, the decline pattern might be different (e.g., news/recent-events topics held up by fresh content LLMs can't answer).
- The analysis uses pageviews, which are themselves measured imperfectly (bots, VPN-distorted geography, domain redirects, mobile-vs-desktop split shifts over time).
- Quality scores are model outputs and reflect the Lift Wing/ORES model's biases.

## 8. Data and code

Everything is in [the career-images repo](https://github.com/tieguy/career-images), under `analysis/vital-articles/`. The FINDINGS.md there has current numbers and regression tables; the README / CLAUDE.md has the full pipeline.

The data pipeline is reproducible from scratch (slowly) on any reasonable machine — Wikimedia's APIs are generous enough that a single-threaded fetch of 5,000 × 12 languages × ~120 months is a one-weekend job.

---

*Draft status: awaiting one more regression rerun with relaxed filters (whole-year baselines + lower MIN_BASELINE) to finalize per-language n's and the R² decomposition. Numbers in the tables above are from the pre-relaxation run and will change slightly.*
