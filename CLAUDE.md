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
