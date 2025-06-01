# Wikipedia Image Diversity Tool - Software Development Specification

## Project Overview

Build a Python tool to improve human diversity in photos used in English Wikipedia articles about jobs and careers. The tool will help identify job-related articles, prioritize them by readership, and facilitate finding more diverse replacement images.

## Core Functionality

### 1. Data Collection & Storage

**Wikidata Query**
- Query Wikidata API for articles where P31 (instance of) equals:
  - profession (Q28640)
  - occupation (Q12737077) 
  - job (Q192581)
  - position (Q4164871)
- Target: Articles about jobs/careers themselves, NOT individual people

**Wikipedia Page Views**
- Fetch 2024 total page views via Wikipedia pageview API
- Calculate average daily views (total views ÷ 365)
- Sort articles by popularity for prioritization

**Google Sheets Integration**
- Use gspread library: https://docs.gspread.org/en/v6.1.4/
- Store data in Google Sheets with columns:
  - Wikidata category (job/profession/occupation/position)
  - Average daily page views (2024)
  - Page name with link to Wikipedia article
  - Thumbnails (URLs as text, regenerated for HTML display)
  - Reviewed (boolean flag, manually set)
  - Non-diverse (boolean flag for inherently non-diverse roles like "pope")

### 2. Image Analysis

**Wikipedia Image Extraction**
- Use Wikipedia API (avoid scraping) to get:
  - Image thumbnails from each article
  - Image captions
- Present in batches of 10 articles for human review

**Openverse Integration**
- Use Openverse REST API (api.openverse.org) to search for alternative images
- API Documentation: Refer to the "API consumer documentation" linked from https://docs.openverse.org/
- Search strategy:
  1. First: "female [job title]" (e.g., "female prime minister")
  2. Fallback: "[job title]" if no results
- Filter results to Wikipedia-compatible licenses using URL parameter: `license=pdm,cc0,by,by-sa`
  - Public Domain Mark (pdm)
  - CC0 (cc0)
  - CC BY (by)
  - CC BY-SA (by-sa)
- API supports anonymous usage with rate limits; registration provides higher limits if needed
- Future iteration: expand beyond "female" to other demographic terms

### 3. Output Generation

**HTML Review Interface**
- Generate static HTML file for each batch of 10 articles
- Display for each article:
  - Article title with link
  - Current Wikipedia images (thumbnails + captions)
  - Suggested Openverse alternatives
  - Links to Wikimedia Commons upload page for each Openverse image
    - Investigate pre-populating Commons upload form with Openverse metadata (similar to Flickr integration)
    - Fall back to standard Commons upload page if pre-population not possible

## Technical Requirements

### Platform & Deployment
- **Code Repository**: GitHub
- **Deployment**: Render Web Services
- **Language**: Python
- Choose appropriate Python version and virtual environment management for GitHub/Render compatibility

### Workflow Design
- **Phase 1**: Run script to populate Google Sheet with lightweight data for ALL articles:
  - Wikidata category
  - Page name with Wikipedia link
  - Average daily page views (2024)
  - Empty columns for reviewed/non-diverse flags
- **Phase 2**: Run script with batch parameters (e.g., "articles 1-10") to:
  - Fetch Wikipedia images and captions for that specific batch
  - Search Openverse for alternative images for that batch
  - Generate HTML review page with current and suggested images
- **Phase 3**: Human reviews HTML, manually updates "reviewed" and "non-diverse" flags in Google Sheet
- **Iteration**: Repeat Phase 2 with next batch (articles 11-20, etc.)

### API Integration
- **Wikidata Query Service**: For initial article discovery
- **Wikipedia API**: For page views and image extraction
- **Openverse API**: For alternative image search
- **Google Sheets API**: Via gspread for data persistence

### Configuration Management
- Handle API keys and credentials appropriately for GitHub/Render environment
- Use environment variables or config files as suitable for deployment platform

## Success Criteria

1. Successfully identifies 10,000+ job/career articles from Wikidata
2. Accurately retrieves and ranks articles by 2024 page view data
3. Presents clear batched review interface showing current Wikipedia images
4. Finds relevant diverse alternative images from Openverse
5. Provides streamlined workflow for uploading alternatives to Wikimedia Commons
6. Maintains progress tracking through Google Sheets integration

## Future Enhancements (Not Required for v1)
- Expand demographic search terms beyond "female"
- More sophisticated image analysis
- Interactive web interface instead of static HTML
- Automated diversity assessment using image recognition

## Notes
- Prioritize using APIs over web scraping
- Keep initial implementation simple and extensible
- Focus on batch processing workflow for human review efficiency
- Ensure all suggested images meet Wikipedia licensing requirements