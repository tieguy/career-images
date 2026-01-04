# Wikipedia Career Image Diversity Tool - Design Document

## Overview

A Toolforge-hosted web app where reviewers work through Wikipedia career articles (ranked by pageviews), assess current images for diversity, find replacements via Openverse, and get everything prepped for a Wikipedia/Commons edit.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Toolforge                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Web App    │  │   MariaDB    │  │   Fetcher    │  │
│  │   (Flask)    │◄─┤  (ToolsDB)   │◄─┤   (CLI)      │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
         │                                    │
         ▼                                    ▼
   ┌──────────┐                    ┌─────────────────────┐
   │ Reviewer │                    │ Wikidata / Wikipedia│
   │ Browser  │                    │ Openverse APIs      │
   └──────────┘                    └─────────────────────┘
```

**Local development**: SQLite instead of MariaDB, same schema.

## Data Model

### Table: `careers`

| Column | Type | Description |
|--------|------|-------------|
| wikidata_id | VARCHAR(20) PK | e.g., "Q1234" |
| name | VARCHAR(255) | e.g., "Nurse" |
| category | ENUM | profession / occupation / job / position |
| wikipedia_url | VARCHAR(512) | Full Wikipedia URL |
| pageviews_total | INT | 2024+2025 combined |
| avg_daily_views | DECIMAL(10,2) | For ranking |
| status | ENUM | unreviewed / needs_image / has_image / not_applicable |
| reviewed_by | VARCHAR(255) | Free text for MVP |
| reviewed_at | DATETIME | When reviewed |
| notes | TEXT | Reviewer notes |
| lede_text | TEXT | First paragraph (cached) |
| images_fetched_at | DATETIME | Cache timestamp |
| lede_fetched_at | DATETIME | Cache timestamp |

### Table: `career_images`

| Column | Type | Description |
|--------|------|-------------|
| id | INT PK | Auto-increment |
| wikidata_id | VARCHAR(20) FK | Links to careers |
| image_url | VARCHAR(512) | Full image URL |
| caption | TEXT | Image caption |
| position | INT | Order in article |
| is_replacement | BOOLEAN | True if Openverse pick |
| source | ENUM | wikipedia / openverse |

## Reviewer Interface

### Main View: Career List

Ranked list of careers by pageviews. Columns: Rank, Career name, Daily views, Status. Click to open detail view.

### Detail View: Single Career

**Section 1: Current Wikipedia State**
- Lede text (first paragraph or two)
- All current images with captions

**Section 2: Openverse Search**
- Search box with user-entered terms (e.g., "male nurse")
- Preset buttons for convenience (male/female/job title only)
- Results filtered to CC-BY-SA, CC-BY, CC0, PDM licenses
- Select button on each result

**Section 3: Edit Preparation** (after selecting image)
- Image metadata (source, author, license)
- Step 1: Commons Upload Wizard link (pre-filled where possible)
- Step 2: Wikipedia edit link + suggested wikitext (copy to clipboard)
- Step 3: Mark complete button

**Section 4: Status**
- Radio buttons: unreviewed / needs_image / has_image / not_applicable
- Notes field
- Save & Next button

## Data Fetching

### Initial Population
1. Query Wikidata for careers (profession/occupation/job/position with English Wikipedia article)
2. Fetch 2024+2025 pageviews from Wikipedia API
3. Store in database

### On-Demand (when reviewer opens a career)
1. Fetch Wikipedia images + captions (if never fetched or stale)
2. Fetch lede text via Wikipedia extracts API
3. Openverse search is always live

### Caching
- Images/lede: Cache for 7 days
- Openverse: Never cache (always fresh)
- Pageviews: Refresh on-demand via CLI command

## Toolforge Deployment

Flask app structure per Toolforge conventions:
```
$HOME/www/python/
├── src/
│   └── app.py          # Flask app entry point (must export `app`)
├── venv/               # Virtual environment
```

Database: ToolsDB MariaDB at `tools.db.svc.wikimedia.cloud`

## Future Enhancements (not MVP)

- LLM analysis of current images to suggest search terms
- LLM filtering of Openverse results (most encyclopedic, shows people, geographic diversity)
- Sort by least recently edited (recently edited may already be improved)
- Backfill command to pre-fetch images for top N careers
- OAuth login for reviewer tracking

## Implementation Phases

### Phase 1: Data Foundation
- `db.py` - SQLite locally, MariaDB on Toolforge
- `fetcher.py` - Wikidata query + pageview collection
- Result: Database with ~10k careers ranked by views

### Phase 2: Web App Skeleton
- Flask app with career list view
- Click through to detail view (placeholders)
- Local only, verify Toolforge compatibility

### Phase 3: Wikipedia Integration
- Fetch images + captions on-demand
- Fetch lede text
- Display in detail view

### Phase 4: Openverse Integration
- Search box with user terms
- Display results with license info
- Select image → show metadata

### Phase 5: Edit Preparation
- Research Commons/Wikipedia URL parameters
- Generate deep-links or copy-paste bundles
- Status tracking

### Phase 6: Polish
- Error handling, empty states
- Basic styling
- Collaborator documentation

## Coordination (MVP)

Email Luis when you've reviewed something. Proper queue system later if needed.
