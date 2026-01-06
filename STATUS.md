# Project Status - Wikipedia Career Image Diversity Tool

## Current State (2025-01-06)

### What This Tool Does
Helps improve human diversity in photos used in English Wikipedia articles about jobs and careers. Provides a web interface to review career articles ranked by pageviews, search for diverse replacement images via Openverse, and prepare edits for Wikipedia.

### Architecture
- **Flask web app** with SQLite database
- **P106-based Wikidata queries** for finding professions (cleaner than class traversal)
- **Async pageview fetching** from Wikipedia API
- **Openverse integration** for finding CC-licensed replacement images
- **PWA support** for mobile use

### Completed Features
1. **Data pipeline** (fetcher.py)
   - P106 (occupation) query approach: finds items used as occupations, filtered by P31 to profession classes
   - ~4,100 careers with English Wikipedia articles
   - Pageview data for 2024-2025

2. **Web interface** (app.py + templates/)
   - Career list ranked by daily views with search and status filter
   - Detail view with Wikipedia lede, images, and review status
   - Openverse search with presets (male/female/job title)
   - Edit preparation: Commons upload wizard link, Wikipedia edit link

3. **Database** (db.py)
   - `careers` table: wikidata_id, name, category, pageviews, status, notes
   - `career_images` table: Wikipedia and Openverse images per career
   - Status workflow: unreviewed → needs_image / has_image / not_applicable

### Open Beads (Issues)

**High Priority:**
- `career-images-bjw` - Fix Commons upload wizard link (parameters not working)
- `career-images-bux` - Upload wizard should use attribution from Openverse

**UI Improvements:**
- `career-images-boh` - Sort by pageview buckets, alphabetically within bucket
- `career-images-6ju` - Add license metadata to thumbnails
- `career-images-t1a` - Add link to source page on thumbnails

**Status Refinement:**
- `career-images-ly5` - Split 'not applicable' into 'not_a_career' and 'gender_specific'
- `career-images-99i` - Rename statuses to mention "diverse images"
- `career-images-1u3` - Ask Anasuya: "diverse images" vs "representative images"?

**Other:**
- `career-images-k0c` - Build test infrastructure
- `career-images-b39` - Wikipedia essay on NPOV for images

### Not Started
- Toolforge deployment
- Google Sheets integration for tracking

## Commands

```bash
# Fetch all careers (takes ~3 minutes)
uv run python fetcher.py fetch

# Run web app
uv run python app.py
# Open http://localhost:5000

# Check stats
uv run python fetcher.py stats
uv run python fetcher.py top 50

# Resume pageview fetching if interrupted
uv run python fetcher.py resume
```

## Key Files
- `fetcher.py` - P106-based Wikidata queries + pageview fetching
- `app.py` - Flask web application
- `db.py` - Database abstraction layer
- `wikipedia.py` - Wikipedia API helpers
- `openverse.py` - Openverse image search
- `career_classes.json` - Cached list of profession-related Wikidata classes
- `careers.db` - SQLite database (not in git)

## Technical Notes

### P106 Query Strategy
The fetcher uses P106 (occupation) property values as the source of professions:
1. Query all items ever used as someone's P106 value
2. Filter to items with P31 (instance of) pointing to profession classes
3. This ensures only legitimate professions (filters out garbage like places, companies)

Class list includes:
- Base: profession, occupation, job, position
- Additional: academic rank, noble title, title of authority

### Database Categories
Items are categorized as: profession, occupation, job, or position (based on P31 type).
