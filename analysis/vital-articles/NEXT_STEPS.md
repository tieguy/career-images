# vital-articles: next steps

This subproject ships a first pass: sample Level-5 Vital Articles, fetch pageviews,
aggregate decline by the 11 editorial topic buckets. Two follow-ups are queued
once the v1 numbers settle. Neither is started.

(The repo's canonical issue tracker is chainlink — `.chainlink/issues.db`. These
are parked here because the items are scoped to this subproject; promote to
chainlink issues if either grows past a weekend's worth of work.)

## 1. Cross-language decline comparison (QID-keyed)

**Question.** Does the same article decline as hard in other language
Wikipedias as it does in English? If decline is LLM-substitution-driven, we
expect the English drop to be deeper (ChatGPT-grade coverage is best in
English). If it's broader attention shift, other large Wikipedias should
decline in step.

**Why it needs a re-shape.** Today the vital-articles schema is title-keyed
(`articles.title` PK) because the upstream Wikipedia:Vital_articles/data/*.json
is title-keyed on en.wikipedia. To compare across languages you need a stable
cross-language key: the Wikidata QID.

**Concrete next steps.**
- Resolve each sampled en.wikipedia title to its QID (one SPARQL query:
  `SELECT ?item ?enwikiTitle WHERE { ?enwiki schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>; schema:name ?enwikiTitle. VALUES ?enwikiTitle { ... } }`
  — batch 500 titles per request, same pattern fetcher.py already uses).
- Add a `wikidata_id` column to `samples` (nullable — some vital articles may
  be redirects or unresolvable).
- Pick a small set of comparison wikis (de, fr, es, ja, zh are obvious starts
  — large enough to have coverage, diverse enough editorially).
- For each comparison wiki, query sitelinks from Wikidata (`?item schema:isPartOf <https://de.wikipedia.org/>`)
  to get the foreign-language title for each QID.
- Reuse `career-cliff/pageviews_api.py` with `project="de.wikipedia"` etc.
  (the helper already takes a project arg). Store per-language monthly views
  in a new table keyed on `(qid, language, year, month)` — don't overload
  `monthly_views`.
- Extend `report.py` (or a new `cross_lang_report.py`) to emit a decline-per-language
  column per bucket, and a per-article table for the articles with the biggest
  en-vs-other gap.

**Data caveat.** Coverage varies: a vital article in English may be a stub or
absent on smaller wikis. Report coverage alongside decline; don't silently
drop articles that are missing on some wikis.

## 2. Singer et al. (2017) information-need classifier

**Background.** Singer, Lemmerich, West et al. 2017 ("Why We Read Wikipedia",
WWW 2017) established a 3-dimensional taxonomy — motivation, information
need (quick_fact / overview / in_depth), prior knowledge (familiar /
unfamiliar) — validated against a 2016 reader survey. The hypothesis is that
"quick fact" lookups are the category most easily substituted by LLMs, so
per-bucket decline should track per-bucket quick-fact share.

**Why we deferred it for v1.** The user wanted a "let the facts speak for
themselves" first pass using pre-existing editorial topic buckets before
adding an LLM-classifier layer. The Singer dimensions are also moving
targets — 2016 session fingerprints may no longer describe 2026 behavior.

**Concrete next steps.**
- Build a content-based classifier (not behavioral) for information need, so
  it's independent of the pageview signal we're trying to explain (avoids
  circularity in any regression). Inputs: article lede + infobox + section
  headings. Labels: quick_fact / overview / in_depth.
- Bootstrap labels from Singer 2017 survey crosswalks if available; otherwise
  hand-label ~200 articles stratified across the 11 Vital buckets, then
  use as prompt exemplars for an LLM few-shot classifier or as a training set
  for a small fine-tune.
- Validate against 2016-19-only behavioral data (per-article session time,
  return rate from Clickstream) — behavioral labels from the stable pre-LLM
  era are a fair target; post-2020 behavior is contaminated by the thing
  we're measuring.
- Add `information_need` as a column on `samples` (or a parallel table if
  multi-label). Re-run the bucket aggregation grouped by information-need
  instead of by editorial topic, and by both jointly.

**Pipeline sketch (from earlier design conversation, verbatim in spirit).**
Two-stage: (1) coarse editorial topic from Vital Articles buckets — free,
already done; (2) content-based information-need classifier on top — adds
the dimension that should correlate with LLM substitutability. Stage 2 is
non-circular w.r.t. pageview decline because its inputs are article
*content*, not article *traffic*.
