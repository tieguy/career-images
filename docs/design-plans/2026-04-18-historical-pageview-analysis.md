# Historical Pageview Decline Analysis Design

## Summary

This subproject adds a standalone historical analysis pipeline to the wikipedia-career-images codebase. The motivation is to replicate, in narrow form, the kind of analysis done in Monperrus's *wikipedia-decline-llm* paper — which examines long-term pageview decline on English Wikipedia — but limited to the roughly 4,000 career-related articles already tracked in the project's database. The pipeline fetches ten years of monthly pageview data (2016 through 2025) from the Wikimedia Pageviews REST API, aggregates it into annual totals, and stores everything in a separate SQLite file that never touches the live application's database.

The approach is deliberately incremental: three scripts run in sequence. The first fetches and persists raw annual totals with resume-on-failure support. The second computes per-year rankings within the career article set and identifies the "ever-top" subset — articles that were among the most-viewed career articles in at least one of the ten years. The third produces a first-pass decline report comparing an early window (2016–2019) against a recent window (2022–2025), exporting a CSV for further analysis. The design reuses the codebase's existing async HTTP patterns and test conventions, while deliberately keeping the analysis code isolated from the Flask application so it can be run as an offline research artifact without deployment concerns.

## Definition of Done

A companion analysis to Monperrus's *wikipedia-decline-llm* paper, narrowed to the career-article domain but broadened to more articles. Done when:

- Historical pageview data for all ~4,000 careers currently in `careers.db` has been fetched from the Wikimedia Pageviews REST API covering ten complete years (2016-01 through 2025-12).
- Annual totals are persisted in a separate SQLite database (`analysis/historical-decline/history.db`), never mixed into the live app's `careers.db`.
- The "ever-top" article set — the union of top-N (N=50 by default, configurable) articles by annual pageviews across any of the ten years — is computed and persisted.
- A first-pass report answers the user's primary question: has absolute pageview volume for these articles declined over the 2016–2025 window, and by how much?
- The subproject can be re-run end-to-end against the current `careers.db` without manual intervention, and resumes cleanly after interruption mid-fetch.
- Analysis output is reproducible: someone cloning the repo and running three scripts in order produces the same dataset and report, modulo new pageviews accrued since.

Explicitly out of scope for this phase: pre-2016 data, mobile-vs-desktop splits, bot-inclusive comparisons, attribution to LLMs specifically, and polished report visualizations (the user will iterate on report details after seeing the first-pass output).

## Glossary

- **Wikimedia Pageviews REST API**: A public REST API (`wikimedia.org/api/rest_v1/metrics/pageviews/...`) that returns per-article view counts for Wikipedia at daily, monthly, or annual granularity. This project calls it with `granularity=monthly` and sums results client-side into annual buckets.
- **Wikidata Q-ID**: A stable numeric identifier for a concept in Wikidata (e.g. `Q28640` for "profession"). Used here as the primary key for career articles, since Wikipedia titles can change over time.
- **P106**: The Wikidata property meaning "occupation." The main codebase queries all items used as someone's P106 value to build the ~4,000 career article list that this pipeline reads.
- **ever-top set**: The union of articles that ranked in the top-N (default 50) most-viewed career articles for at least one year in the 2016–2025 window. A career article need only be top-N in a single year to be included.
- **fallen giants**: Informal label (used in the report phase) for articles that were historically high-traffic but have experienced significant pageview decline — the primary subject of the analysis.
- **Monperrus / wikipedia-decline-llm**: A paper by Martin Monperrus studying long-term pageview decline on English Wikipedia, used as the intellectual reference point for this subproject's research question.
- **pagecounts-ez**: A pre-2016 Wikipedia pageview data source with a methodology break relative to the current Pageviews REST API. Mentioned as a possible future extension for extending the analysis window back before 2016, but explicitly out of scope for this phase.
- **dense rank**: A ranking method where tied values receive the same rank and no ranks are skipped (e.g. two articles with equal views both get rank 1, and the next distinct value gets rank 2). Used in `compute_rankings.py`; ties are broken deterministically by Q-ID lexicographic order.
- **title drift**: The phenomenon where a Wikipedia article is renamed between 2016 and now, causing the historical API query under the old title to return a 404 or all-zero response. Logged as `missing` status in `fetch_log`; automatic redirect resolution is deferred.
- **Toolforge**: The Wikimedia Foundation's hosted computing platform where the live Flask app can be deployed, using MariaDB instead of SQLite. The analysis subproject deliberately avoids the app's database abstraction because it has no Toolforge deployment surface.
- **aiohttp**: A Python async HTTP client library. The existing `fetcher.py` already uses it for concurrent pageview fetching; `fetch_history.py` follows the same pattern with a bounded `TCPConnector`.
- **fetch_log**: A SQLite table in `history.db` that records fetch status (`ok`, `missing`, or `error`) per career article, enabling the fetcher to resume cleanly after interruption.

## Architecture

A standalone read-only sibling to the live Flask app. Three scripts, run in order, produce a self-contained analytical dataset in a separate SQLite file.

**Location:** `analysis/historical-decline/` — new subdirectory inside the existing repo. Shares the repo's `uv` environment and dependencies. Never writes to `careers.db`.

**Pipeline:**

1. **`fetch_history.py`** reads the career list (Wikidata Q-IDs and current Wikipedia titles) from `careers.db` as input, then for each career makes a single call to the Wikimedia Pageviews REST API with `granularity=monthly`, `agent=user`, `access=all-access`, `start=20160101`, `end=20251231`. The 120 monthly rows returned per article are summed client-side into 10 annual buckets and written to `history.db`. Monthly data is not persisted. Fetch state is tracked in `fetch_log` so the script can resume cleanly after interruption.

2. **`compute_rankings.py`** reads `annual_totals` from `history.db`, computes per-year rank for every article within the career set, and produces the `ever_top` table: one row per article that appeared in the top-N of any year 2016–2025. N is a CLI flag, default 50.

3. **`report.py`** (or Jupyter notebook) reads `ever_top` plus `annual_totals` and emits the primary decline analysis (median % change, per-article decline curves, "fallen giants" table). Exact report shape is deferred — the design commits only to producing a coherent first-pass output.

**Data boundary:** `careers.db` is read-only from this subproject's perspective. `history.db` is owned by this subproject. No cross-database writes. Schemas are independent.

## Existing Patterns

Investigation found two relevant patterns in the existing codebase.

**Async pageview fetching (`fetcher.py`).** Already uses `aiohttp.ClientSession` with a bounded `TCPConnector` for concurrent pageview API calls, and already has a resume-on-failure pattern (`cmd_resume` + `get_careers_needing_pageviews`). The new `fetch_history.py` follows the same shape: async fetcher with concurrency-limited connector, batch collection, and a fetch_log-backed resume path. The only differences are: (a) date range is fixed at 2016–2025 instead of 2024+2025, (b) each response produces ten annual rows instead of a single total.

**Database abstraction (`db.py`).** The live app uses a DB abstraction that auto-selects SQLite locally and MariaDB on Toolforge. This subproject does not use that abstraction — `history.db` is local-only, SQLite-only, and lives outside the app's deployment surface. A small module `analysis/historical-decline/history_db.py` wraps `sqlite3.connect()` directly with a handful of helper functions (`upsert_annual_totals`, `get_fetch_status`, `write_ever_top`). Divergence is justified: Toolforge deployment is irrelevant for an offline research artifact, and pulling in the full abstraction would couple the subproject to app migrations.

**Test layout (`tests/test_*.py`).** Existing test files use `pytest` with `responses` for HTTP mocking. The subproject's tests follow the same convention: `tests/test_historical_fetch.py`, `tests/test_historical_rankings.py`, with mocked Pageviews API responses.

## Implementation Phases

### Phase 1: Subproject Scaffolding and Schema

**Goal:** Create the subdirectory structure, the `history.db` schema, and a DB helper module, with infrastructure verification only (no functional code yet).

**Components:**

- `analysis/historical-decline/` directory with `__init__.py`, `README.md` (one-paragraph purpose), and `schema.sql`.
- `analysis/historical-decline/history_db.py` — thin SQLite wrapper exposing `connect()`, `init_schema()`, and basic upsert helpers. No business logic.
- `analysis/historical-decline/init_db.py` — CLI entrypoint that creates `history.db` (empty) from `schema.sql`. Idempotent.

**Schema (`schema.sql`):**

```sql
CREATE TABLE IF NOT EXISTS annual_totals (
    wikidata_qid TEXT NOT NULL,
    title        TEXT NOT NULL,
    year         INTEGER NOT NULL,
    views        INTEGER NOT NULL,
    rank         INTEGER,
    PRIMARY KEY (wikidata_qid, year)
);

CREATE TABLE IF NOT EXISTS ever_top (
    wikidata_qid   TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    first_top_year INTEGER NOT NULL,
    last_top_year  INTEGER NOT NULL,
    years_in_top   INTEGER NOT NULL,
    peak_rank      INTEGER NOT NULL,
    peak_year      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_log (
    wikidata_qid TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    fetched_at   TIMESTAMP,
    status       TEXT NOT NULL,    -- 'ok' | 'missing' | 'error'
    error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_annual_totals_year_rank ON annual_totals(year, rank);
CREATE INDEX IF NOT EXISTS idx_fetch_log_status ON fetch_log(status);
```

**Dependencies:** None (first phase).

**Done when:** `uv run python analysis/historical-decline/init_db.py` creates an empty `history.db` with the three tables; running it twice is a no-op; `uv run pytest` still passes with zero new tests expected at this stage.

### Phase 2: Historical Pageview Fetcher

**Goal:** Implement `fetch_history.py` to pull and persist 2016–2025 annual pageview totals for every career in `careers.db`, with resume-on-failure.

**Components:**

- `analysis/historical-decline/fetch_history.py` — CLI with subcommands `fetch` (full) and `resume` (only rows missing or in `error` state). Async fetcher built on `aiohttp`, mirroring `fetcher.py:fetch_pageviews_batch` structure. Reads career list from `careers.db` via the existing `db.py` accessor (read-only). Writes `annual_totals` and `fetch_log` to `history.db` via `history_db.py`.
- Title-drift handling: on a 404 or all-zero response, log status `missing` in `fetch_log` with the queried title in `error`. No automatic redirect resolution in this phase — flagged manually for later. Rationale: we expect a small minority of careers to have drifted titles, and a manual triage pass is cheaper than building redirect resolution upfront.
- `tests/test_historical_fetch.py` — unit tests with `responses`-mocked API for: happy path (120 months → 10 annual rows, correct sums), 404 handling, all-zero handling, resume behavior (only re-fetches rows not in `ok` state), empty month handling (API returns fewer than 120 rows for newer articles).

**Contract — Pageviews API call:**

```
GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/
    en.wikipedia/all-access/user/<url-encoded-title>/monthly/2016010100/2025123100
```

Response `items[]` objects: `{timestamp: "YYYYMM0100", views: <int>}`. The fetcher sums `views` per calendar year 2016–2025.

**Dependencies:** Phase 1.

**Done when:** Running `uv run python analysis/historical-decline/fetch_history.py fetch` against a limited career slice (e.g. `--limit 10`) populates `annual_totals` with 100 rows (10 articles × 10 years) and `fetch_log` with 10 `ok` entries; `resume` after a simulated mid-fetch kill re-fetches only incomplete rows; all new tests in `tests/test_historical_fetch.py` pass.

### Phase 3: Ranking and Ever-Top Computation

**Goal:** Implement `compute_rankings.py` to produce per-year ranks and the ever-top union set.

**Components:**

- `analysis/historical-decline/compute_rankings.py` — reads `annual_totals`, computes rank within each year (dense rank, highest views = rank 1), writes `rank` back to `annual_totals`, then computes `ever_top` as the union of articles with `rank <= N` in any year. Default `N=50`, CLI flag `--top-n`.
- `tests/test_historical_rankings.py` — unit tests using a small fixture dataset for: rank correctness (ties handled deterministically by Q-ID lexicographic order), ever_top membership (article ranked 1 in 2017 and 200 elsewhere is included for any N >= 1; article never in top-N is excluded), `peak_rank`/`peak_year` computation, `first_top_year`/`last_top_year`/`years_in_top` correctness.

**Dependencies:** Phase 2 (needs populated `annual_totals`).

**Done when:** On the real populated dataset, `compute_rankings.py --top-n 50` produces a non-empty `ever_top` whose size falls within the predicted 100–400 range; re-running with `--top-n 25` produces a strict subset; all tests in `tests/test_historical_rankings.py` pass.

### Phase 4: First-Pass Report

**Goal:** Produce a minimally coherent report that answers "have pageviews declined?" on the ever-top set.

**Components:**

- `analysis/historical-decline/report.py` — reads `ever_top` and `annual_totals`, computes median percent change in annual views between an early-window baseline (2016–2019) and a recent window (2022–2025), prints a summary table of per-article baseline-vs-recent totals, and writes a CSV export to `analysis/historical-decline/output/decline_summary.csv` for further ad-hoc analysis.
- No visualization required in this phase; the user has explicitly deferred report details.
- `tests/test_historical_report.py` — smoke test on a tiny fixture that asserts the report runs end-to-end and produces a CSV with the expected columns.

**Dependencies:** Phase 3.

**Done when:** `uv run python analysis/historical-decline/report.py` against the real dataset prints a non-empty decline summary and writes a valid CSV; the smoke test passes.

## Additional Considerations

**Title drift triage.** Articles renamed on Wikipedia between 2016 and now will appear as `missing` in `fetch_log`. If the `missing` count is significant (>5% of careers), a follow-up task should add automatic redirect resolution using the MediaWiki `redirects` API or Wikidata sitelink history. Deferred until data tells us it's needed.

**Top-N sensitivity.** The choice of N=50 is arbitrary. The user may want to eyeball the resulting `ever_top` size and adjust. Because `compute_rankings.py` is cheap to rerun (operates entirely on the existing local DB, no network), iterating on N is trivial.

**Ranking is within-set, not Wikipedia-global.** "Top-50" here means "top 50 most-viewed articles among our ~4,000 career articles in year Y," not "top 50 on English Wikipedia." This is correct for the research question but must be stated in the report's preamble to avoid misinterpretation.

**Reproducibility.** The dataset produced by `fetch_history.py` changes monotonically as new months accrue and `careers.db` picks up newly-added careers. A given report run should note the `careers.db` row count and `fetch_log` timestamps in its output for traceability.

**Future extensions (not in scope, listed for design clarity).** Pre-2016 data via `pagecounts-ez` (flagged with the methodology-break caveat), mobile-vs-desktop splits (requires different API access values), category cuts (joining against `careers.db` category field), comparison against an LLM-launch inflection date.
