# Project Status - Wikipedia Career Image Diversity Tool

## Current State (2025-01-04)

### Completed
1. **db.py** - Database abstraction (SQLite local, MariaDB Toolforge)
   - `careers` table: wikidata_id, name, category, wikipedia_url, pageviews, status, notes, lede_text
   - `career_images` table: images per career, supports wikipedia + openverse sources
   - Status workflow: unreviewed → needs_image / has_image / not_applicable

2. **fetcher.py** - Data collection CLI
   - Wikidata SPARQL query for careers (profession/occupation/job/position)
   - Async pageview fetching (~50 concurrent, 22 seconds for 11.5k careers)
   - Commands: `fetch`, `resume`, `stats`, `top N`

3. **wikipedia.py** - Wikipedia API integration
   - `fetch_career_data(url)` - returns lede + images
   - Handles redirects (e.g., "Nurse" → "Nursing")
   - Filters out template/icon images

4. **Flask web app** (app.py + templates/)
   - List view: careers ranked by pageviews, pagination, status filter
   - Detail view: lede text, Wikipedia images, status form
   - "Save & Next" workflow for reviewers

5. **Data loaded**: 11,572 careers in careers.db

### Completed (just added)
- **openverse.py** - Openverse API integration
  - `search_images(query)` - Search with CC-compatible license filter
  - `get_image_detail(id)` - Get full image metadata
  - `generate_attribution()` - Format attribution text

- **Edit preparation in detail view**
  - Openverse search box with presets (male/female/job title)
  - Select button saves replacement to database
  - Shows selected replacement with:
    - Link to Commons Upload Wizard
    - Link to Wikipedia edit page
    - Wikitext template to copy

### Not Started
- Toolforge deployment
- Polish/error handling
- Better Commons upload URL pre-filling (if possible)

## Next Steps

### Openverse Integration
Create `openverse.py`:
- Search endpoint: `https://api.openverse.org/v1/images/`
- License filter: `license=pdm,cc0,by,by-sa`
- Search box in detail view with user-entered terms
- Display results with select button

### Edit Preparation
Research and implement:
- Commons Upload Wizard URL pre-filling
- Wikipedia edit URL with suggested wikitext
- Copy-to-clipboard for wikitext

## Commands

```bash
# Fetch all careers
uv run python fetcher.py fetch

# Run web app
uv run python app.py
# Open http://localhost:5000

# Check stats
uv run python fetcher.py stats
uv run python fetcher.py top 50
```

## Key Files
- Design doc: `docs/plans/2025-01-04-collaborative-reviewer-tool-design.md`
- Database: `careers.db` (SQLite)
- Dependencies: `pyproject.toml` (requests, aiohttp, flask)
