# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Note**: This project uses [chainlink](https://github.com/acj/chainlink) for issue tracking. Use `chainlink` commands instead of markdown TODOs.

## Project Overview

This is a Python tool to improve human diversity in photos used in English Wikipedia articles about jobs and careers. The project queries Wikidata for career-related articles, fetches Wikipedia pageview statistics, and provides a web interface for reviewing images and finding diverse replacements via Openverse.

## Architecture

The project uses a **Flask web app with SQLite database**:

- `app.py` - Flask web application for reviewing career images
- `db.py` - Database abstraction (SQLite locally, MariaDB on Toolforge)
- `fetcher.py` - Fetches career data from Wikidata and pageviews from Wikipedia API
- `wikipedia.py` - Wikipedia API helpers for fetching article content/images
- `openverse.py` - Openverse API integration for finding diverse replacement images
- `commons.py` - Wikimedia Commons API integration for browsing category files
- `migrations/` - Database migration scripts
- `scripts/` - Utility scripts (audit.py, gsheets.py, wiki-*.sh)
- `analysis/career-cliff/` - Self-contained subproject analyzing 2016–2025 pageview decline for career articles; has its own `history.db` and does not modify `careers.db` (see Career Cliff Pageview Analysis below)
- `analysis/vital-articles/` - Self-contained cross-language pageview decline analysis using Wikipedia's Level-5 Vital Articles list as a sampling frame; has its own `vital.db` and is read-only against all other data (see Vital Articles Cross-Language Analysis below)

### Key Design Decisions

- **Wikidata Query Strategy**: Uses P106 (occupation) property to find professions:
  - Queries all items used as someone's P106 value (i.e., things listed as occupations)
  - Filters to items with P31 (instance of) pointing to profession-related classes
  - This ensures only legitimate professions (filters out garbage like places, companies)
  - Total: ~4,000 careers with English Wikipedia articles
- **Database**: SQLite for local dev (`careers.db`), auto-detects Toolforge for MariaDB
- **Pageview Data**: Async fetching from Wikipedia's pageview API with rate limiting
- **Image Search**: Openverse API for finding CC-licensed diverse replacement images

## Development Commands

### Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync
```

### Running the Web App

```bash
uv run python app.py
```

Then open http://localhost:5000

### Fetching/Updating Career Data

```bash
uv run python fetcher.py fetch           # Full fetch from Wikidata + pageviews
uv run python fetcher.py fetch --limit 50  # Limited fetch for testing
uv run python fetcher.py resume           # Continue fetching pageviews for incomplete records
uv run python fetcher.py stats            # Show dataset statistics
uv run python fetcher.py top 20           # Show top 20 careers by pageviews
uv run python fetcher.py fetch-commons   # Backfill Commons categories (P373) for existing careers
```

### Career Cliff Pageview Analysis (subproject)

Last verified: 2026-04-18

Self-contained analysis of 2016 through Q1 2026 pageview decline for career articles, living in `analysis/career-cliff/`. Reads `careers.db` read-only; all writes go to its own `analysis/career-cliff/history.db` (gitignored). The subproject's `output/` directory is mostly gitignored except for the publishable `blog_post.md` and `charts/*.png` artifacts. Tests live in `tests/test_historical_*.py` (name retained from when the subproject was called `historical-decline`).

**Fetching is synchronous** (`requests`, not `aiohttp`). An earlier async version with 50-way concurrency triggered Wikimedia rate limits hard (1,752 false-positive `missing` rows from 429 storms). Sequential fetching with a small `--delay` between requests (default 0.1s) stays under policy and completes ~4,000 articles in ~30 minutes with essentially zero retries. Keep it synchronous when working on this subproject.

Pipeline (run in order):
```bash
uv run python analysis/career-cliff/init_db.py              # Create history.db from schema.sql
uv run python analysis/career-cliff/fetch_history.py fetch  # Sync-fetch monthly pageviews 2016-01 → 2026-03, resumable
uv run python analysis/career-cliff/compute_rankings.py     # Compute annual ranks + ever-top set
uv run python analysis/career-cliff/report.py               # Print decline summary + CSV (2016-19 vs 2025-04..2026-03)
uv run --extra analysis python analysis/career-cliff/blog_charts.py  # Render blog PNGs (requires matplotlib + statsmodels)
```

Data layout: `monthly_views` stores per-month rows for the full fetch range; `annual_totals` is a derived rollup for complete years only (missing a year just means that year wasn't complete in the fetch window, e.g., 2026). `ever_top` is the union of articles that ranked top-50 in any year.

Modules: `pageviews_api.py` (URL + response helpers), `history_db.py` (connection + write helpers), `rankings.py` (rank/ever-top logic), `report.py` (per-month-normalized decline analysis), `blog_charts.py` (matplotlib PNGs). `schema.sql` + `init_db.py` initialize the DB; `fetch_history.py` does the fetching; `compute_rankings.py` is the ranking CLI.

Dependency groups: the main subproject uses only stdlib + `requests` (already a runtime dep). `blog_charts.py` requires `matplotlib` and `statsmodels` (for LOESS smoothing), declared under the `analysis` optional-dependencies group — install with `uv sync --extra dev --extra analysis` (both extras together, since `uv sync --extra analysis` alone drops the `dev` extra).

Design doc: `docs/design-plans/2026-04-18-historical-pageview-analysis.md`. Implementation plan: `docs/implementation-plans/2026-04-18-historical-pageview-analysis/`. Blog draft: `analysis/career-cliff/output/blog_post.md`. (Design/implementation-plan filenames retained for history; the subproject directory was renamed from `historical-decline` to `career-cliff` on 2026-04-18 to match the published blog post slug.)

### Vital Articles Cross-Language Analysis (subproject)

Last verified: 2026-04-22

Self-contained cross-language pageview-decline analysis using Wikipedia's [Level-5 Vital Articles](https://en.wikipedia.org/wiki/Wikipedia:Vital_articles/Level/5) as a topic-stratified sampling frame. Lives in `analysis/vital-articles/`, writes to its own `vital.db` (gitignored). 5,000 articles sampled with stratification across 11 editorial buckets. Broader in scope than career-cliff (which was just careers); intended to test how much of Wikipedia's traffic decline is topic-specific vs wiki-wide.

**Hypothesis being tested**: LLMs (especially ChatGPT) are substituting for Wikipedia reference lookups. If true, the decline should be steeper in markets with high LLM penetration and in reference-like content, and weaker in markets where ChatGPT is blocked (zh, fa, ru).

Pipeline (run in order, per-language fetchers are resumable):
```bash
uv run python analysis/vital-articles/init_db.py                         # Create vital.db
uv run python analysis/vital-articles/fetch_vital_list.py                # Pull on-wiki JSON (Vital Articles topic assignment)
uv run python analysis/vital-articles/sample.py                          # Stratified 5k sample (seed pinned)
uv run python analysis/vital-articles/fetch_pageviews.py fetch           # en pageviews (title-keyed, sync)
uv run python analysis/vital-articles/fetch_qids.py                      # Resolve titles → Wikidata QIDs via SPARQL
uv run python analysis/vital-articles/fetch_sitelinks.py                 # Per-QID sitelinks across all wikis
uv run python analysis/vital-articles/coverage_report.py                 # Which languages clear the 80% coverage bar
uv run python analysis/vital-articles/fetch_cross_lang_pageviews.py fetch --all-viable   # Non-en pageviews (qid-keyed)
uv run python analysis/vital-articles/fetch_freshness.py --all-viable    # Current revision timestamp per (QID, language)
uv run python analysis/vital-articles/fetch_quality_agnostic.py --all-viable  # Lift Wing language-agnostic articlequality
uv run python analysis/vital-articles/fetch_xtools.py --all-viable       # XTools articleinfo (revs, editors, watchers, etc.)
uv run python analysis/vital-articles/report.py                          # Per-bucket decline report (en only)
uv run --extra analysis --extra dev python analysis/vital-articles/cross_lang_charts.py  # Render all cross-language PNGs
uv run --extra analysis --extra dev python analysis/vital-articles/multivariate_cross_lang.py  # Per-language + pooled OLS
```

**Sync + small-delay convention inherited from career-cliff** — concurrent async against Wikimedia's pageviews API triggered rate-limit storms in the past. All fetchers here are sequential per-project with a default 0.1s inter-request delay. For large fanouts, run separate language processes in parallel (different project subdomains = different rate-limit buckets at WMF).

**SQLite WAL mode is required**: multiple background fetchers write concurrently (pageviews, freshness, quality, XTools all hit different tables). `vital_db.py` sets `PRAGMA journal_mode=WAL` and `timeout=30.0` on every connection to avoid "database is locked" errors.

**Viable-language set (12)** — those clearing the 80% sitelink-coverage bar for a 5k en-sample article set: **en, es, fr, de, zh, ru, it, ar, pt, fa, ja, uk**. 19 additional wikis are in a 50–80% "partial" tier (pl, ko, nl, sv, etc.) but were not fetched in the v1 pass.

**Schema (vital.db)** — core tables:
- `articles`, `article_topics` — on-wiki Vital Articles list and topic→article mapping
- `samples` — stratified 5k sample of Level-5 articles, with `wikidata_id` column populated by `fetch_qids.py`
- `monthly_views` — en-only, title-keyed (parallel to career-cliff's table)
- `cross_lang_monthly_views` — non-en, (qid, language) keyed
- `sitelinks` — (qid, language, foreign_title) mapping from Wikidata
- `article_freshness`, `article_quality_score`, `article_stats` — all keyed on (qid, language)
- `article_quality` (en-only, class-probability per en.wiki-articlequality model) and `article_quality_xlang` (per-language class model, partial — superseded by `article_quality_score` which uses Lift Wing's language-agnostic model)

**Two quality-model layers exist** because Lift Wing has two families:
1. Per-language `{lang}wiki-articlequality` models return Stub..FA class probabilities, but only exist for 6 wikis (en/fr/pt/ru/uk/fa on our viable set). Stored in `article_quality` (en) and `article_quality_xlang` (partial).
2. Language-agnostic `articlequality` model returns a 0–1 scalar, works for every WMF wiki. Stored in `article_quality_score`. **This is the primary quality variable for cross-language regressions** since it's comparable across all 12 languages.

Modules: `pageviews_api.py` (imported from career-cliff, `extract_monthly_views` shared), `vital_db.py` (connection + migration), `report.py` (en-only per-bucket decline table), `cross_lang_charts.py` (all PNGs for en + cross-lang views), `multivariate_cross_lang.py` (per-language + pooled OLS with language fixed effects).

Dependency groups: analysis uses the `analysis` + `dev` extras (same as career-cliff). statsmodels's formula API is currently incompatible with this repo's scipy version, so `multivariate_cross_lang.py` implements OLS directly on numpy + scipy.stats (no smf).

**Output directory** `analysis/vital-articles/output/` is mostly gitignored. Whitelist: `charts/*.png`, `representative_articles.txt`, `FINDINGS.md`. Per-article CSVs and intermediate outputs stay local.

**Status snapshot (2026-04-22)**: Full data pipeline complete for all 12 languages. See `analysis/vital-articles/FINDINGS.md` for the current-state summary including key numbers, regression results, and open questions.

### Dependencies

- `flask` - Web framework
- `requests` - Sync HTTP client for Wikidata
- `aiohttp` - Async HTTP client for pageview fetching
- `sqlite3` - Database (standard library)

## Data Flow

1. `fetcher.py fetch` → Queries Wikidata SPARQL for career articles
2. `fetcher.py` → Async fetches Wikipedia pageviews, stores in `careers.db`
3. `app.py` → Reads from database, displays ranked careers
4. `wikipedia.py` → On-demand fetches article lede and images
5. `openverse.py` → Searches for diverse replacement images
6. `commons.py` → Fetches Commons category files, subcategories, and metadata

## Database Schema

Main tables in `careers.db`:
- `careers` - Career entries with pageviews, review status, notes
- `career_images` - Images associated with careers (from Wikipedia or Openverse)

### Review Statuses (Wikipedia articles)
- `unreviewed` - Not yet reviewed
- `no_picture` - Article has no lead image (auto-detected or manually set)
- `needs_diverse_images` - Has images but needs more diversity
- `has_diverse_images` - Already has diverse representation
- `not_a_career` - Wikidata misclassification, not actually a career
- `gender_specific` - Legitimately gender-specific role (e.g., "abbess")

### Commons Review Statuses
- `unreviewed` - Commons category not yet reviewed for diversity
- `needs_diversity` - Category images lack diversity
- `has_diversity` - Category already has diverse representation
- `not_applicable` - Not relevant for diversity review

## Key Implementation Details

### Commons Category Integration
- Wikidata P373 property links occupations to their Commons categories
- `fetcher.py fetch-commons` backfills this for existing careers (~43% have a linked category)
- `commons.py` uses the MediaWiki API generator query to fetch category files with thumbnails in one call
- Subcategory browsing uses the `categorymembers` API with `cmtype=subcat`
- Pagination uses `gcmcontinue` tokens (not offset-based)

### Wikidata Query
The fetcher uses P106 (occupation) values filtered by P31 (instance of) to career classes. The class list in `career_classes.json` includes:
- Base classes: profession (Q28640), occupation (Q12737077), job (Q192581), position (Q4164871)
- Additional types: academic rank (Q486983), noble title (Q355567), title of authority (Q480319)

### Category Mapping
`fetcher.py:get_category_from_type()` maps Wikidata Q-IDs to normalized categories (profession, occupation, job, position) for the database schema.

### Async Pageview Fetching
Uses `aiohttp` with rate limiting to efficiently fetch pageviews for thousands of articles.
