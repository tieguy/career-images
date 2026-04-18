# Historical Pageview Decline Analysis

Companion analysis to Monperrus's [wikipedia-decline-llm](https://github.com/monperrus/wikipedia-decline-llm) paper, narrowed to the ~4,000 career articles in `careers.db`.

Asks: have Wikipedia pageviews for career articles declined over 2016–2025, and which articles fell the furthest?

## Pipeline

Run in order:

```bash
uv run python analysis/historical-decline/init_db.py
uv run python analysis/historical-decline/fetch_history.py fetch
uv run python analysis/historical-decline/compute_rankings.py
uv run python analysis/historical-decline/report.py
```

## Data

All outputs go to `analysis/historical-decline/history.db` (SQLite). The live app's `careers.db` is read-only from this subproject's perspective.

## Design

See `docs/design-plans/2026-04-18-historical-pageview-analysis.md`.
