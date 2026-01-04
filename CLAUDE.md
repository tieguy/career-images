# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Note**: This project uses [bd (beads)](https://github.com/steveyegge/beads) for issue tracking. Use `bd` commands instead of markdown TODOs. See AGENTS.md for workflow details.

## Project Overview

This is a Python tool to improve human diversity in photos used in English Wikipedia articles about jobs and careers. The project queries Wikidata for career-related articles, fetches Wikipedia pageview statistics, and will eventually facilitate finding more diverse replacement images through Openverse integration and Google Sheets tracking.

## Architecture

The project follows a **two-phase batch processing workflow**:

1. **Data Collection Phase** (`list-fetcher.py`): Queries Wikidata for career/occupation articles and enriches them with 2024 Wikipedia pageview metrics, outputting to `careers_data.json`
2. **Data Processing Phase** (`list-enrich.py`): Reads the JSON data and provides analysis, sorting, searching, and CSV export capabilities

### Key Design Decisions

- **Wikidata Query Strategy**: Uses direct P31 (instance of) queries for four specific career-related entities:
  - Q28640 (profession)
  - Q12737077 (occupation)
  - Q192581 (job)
  - Q4164871 (position)
- **Pageview Data**: Uses Wikipedia's monthly pageview API for all of 2024 (January-December), calculating both monthly and daily averages
- **Rate Limiting**: Built-in 0.1 second delay between Wikipedia API calls to respect rate limits
- **Data Structure**: JSON output includes both human-readable and URL-encoded Wikipedia titles for API compatibility

### Future Architecture (per spec.md)

The spec.md file outlines planned features not yet implemented:
- Google Sheets integration via gspread for persistent tracking
- Batch-based image extraction from Wikipedia articles
- Openverse API integration for finding diverse alternative images
- HTML review interface generation
- Wikimedia Commons upload workflow

## Development Commands

### Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management. Dependencies are defined in `pyproject.toml`.

**Install dependencies:**
```bash
uv sync
```

### Running the Scripts

**Fetch career data from Wikidata and Wikipedia:**
```bash
uv run python list-fetcher.py              # Full data collection
uv run python list-fetcher.py 50           # Development mode with 50-article limit
uv run python list-fetcher.py --sheets     # Full collection + export to Google Sheets
uv run python list-fetcher.py 50 --sheets  # Development mode + Google Sheets export
```

**Process and analyze the collected data:**
```bash
uv run python list-enrich.py               # Show top 20 careers (default)
uv run python list-enrich.py top 50        # Show top 50 careers
uv run python list-enrich.py bottom 20     # Show bottom 20 careers
uv run python list-enrich.py stats         # Display dataset statistics
uv run python list-enrich.py csv           # Export to careers_ranked.csv
uv run python list-enrich.py csv custom.csv  # Export to custom filename
uv run python list-enrich.py search "engineer"  # Search for careers
```

### Dependencies

The project uses Python 3.13+ (system Python). Required libraries:
- `requests>=2.31.0` - API calls to Wikidata and Wikipedia
- `gspread>=6.0.0` - Google Sheets integration (optional)
- Standard library: `json`, `csv`, `time`, `datetime`, `sys`, `urllib.parse`, `os`

Dependencies are managed via uv and defined in `pyproject.toml`.

### Google Sheets Integration (Optional)

To export data to Google Sheets alongside JSON output:

1. **Set up Google Cloud credentials:**
   - Create a Google Cloud project
   - Enable the Google Sheets API
   - Create a service account and download the JSON credentials file
   - Share your target Google Sheet with the service account email address

2. **Configure environment variables:**
   ```bash
   export GOOGLE_SHEETS_CREDENTIALS="/path/to/credentials.json"
   export GOOGLE_SHEET_NAME="Career Images Data"
   ```

3. **Run with `--sheets` flag:**
   ```bash
   uv run python list-fetcher.py --sheets
   ```

The script will create/update a worksheet named "Career Data" with columns matching the spec.md design:
- Rank (by pageviews)
- Career Name, Wikipedia Title, Pageview metrics
- Wikipedia URL, Wikidata Item
- Reviewed (empty, for manual tracking)
- Non-Diverse (empty, for manual flags)

Data is automatically sorted by pageviews (highest first) for easy prioritization.

## Data Flow

1. `list-fetcher.py` → Queries Wikidata SPARQL endpoint
2. `list-fetcher.py` → Fetches Wikipedia monthly pageviews for 2024
3. `list-fetcher.py` → Outputs `careers_data.json` with metadata and career entries
4. `list-fetcher.py` → Optionally exports to Google Sheets (if `--sheets` flag used)
5. `list-enrich.py` → Reads `careers_data.json` for analysis/export

## Key Implementation Details

### URL Encoding in list-fetcher.py
The script maintains both encoded and readable Wikipedia titles:
- `wikipedia_title_encoded`: Used for API calls (preserves URL encoding)
- `wikipedia_title`: Human-readable version (decoded, underscores replaced with spaces)

### Logging System in list-fetcher.py
Custom `log()` function provides timestamped output with severity levels (INFO, WARNING, ERROR). Progress is logged every 100 articles during pageview collection with ETA calculation.

### Pageview Calculation
- Queries monthly data from January 1, 2024 to December 31, 2024
- Calculates `avg_daily_views` as: (total_views / months_counted / 30.44)
- Handles missing data gracefully by setting views to 0
