# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Note**: This project uses [bd (beads)](https://github.com/steveyegge/beads) for issue tracking. Use `bd` commands instead of markdown TODOs. See AGENTS.md for workflow details.

## Project Overview

This is a Python tool to improve human diversity in photos used in English Wikipedia articles about jobs and careers. The project queries Wikidata for career-related articles, fetches Wikipedia pageview statistics, and provides a web interface for reviewing images and finding diverse replacements via Openverse.

## Architecture

The project uses a **Flask web app with SQLite database**:

- `app.py` - Flask web application for reviewing career images
- `db.py` - Database abstraction (SQLite locally, MariaDB on Toolforge)
- `fetcher.py` - Fetches career data from Wikidata and pageviews from Wikipedia API
- `wikipedia.py` - Wikipedia API helpers for fetching article content/images
- `openverse.py` - Openverse API integration for finding diverse replacement images

### Key Design Decisions

- **Wikidata Query Strategy**: Uses P31 (instance of) queries with explicit list of career-related classes:
  - Base: profession, occupation, job, position, type of position
  - Subclasses: legal profession, health profession, medical profession, military profession, etc.
  - Total: ~12,000 careers with English Wikipedia articles
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

## Database Schema

Main tables in `careers.db`:
- `careers` - Career entries with pageviews, review status, notes
- `career_images` - Images associated with careers (from Wikipedia or Openverse)

## Key Implementation Details

### Wikidata Query
The fetcher uses an explicit list of Wikidata classes rather than P279* traversal (which times out). New profession subclasses can be added to the VALUES list in `fetcher.py`.

### Category Mapping
`db.py` contains `CATEGORY_MAP` which maps Wikidata Q-IDs to normalized categories (profession, occupation, job, position) for the database schema.

### Async Pageview Fetching
Uses `aiohttp` with rate limiting to efficiently fetch pageviews for thousands of articles.
