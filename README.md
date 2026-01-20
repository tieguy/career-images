# Wikipedia Career Images

Improving human diversity in photos used on English Wikipedia articles about jobs and careers.

## The Problem

Many Wikipedia articles about professions use lead images that don't reflect the diversity of people in those fields. A single stock photo can shape how millions of readers perceive who belongs in a career.

## The Solution

This tool helps volunteers systematically review career articles and find better, more representative images:

1. **Discover** - Browse ~4,000 career articles ranked by pageviews (highest-impact first)
2. **Review** - See each article's current lead image and assess whether it could be more inclusive
3. **Search** - Find CC-licensed replacement images via Openverse
4. **Track** - Mark articles as reviewed, needing attention, or already diverse

## Getting Started

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run python app.py
```

Open http://localhost:5000 to start reviewing.

## How It Works

The tool pulls career data from Wikidata (professions, occupations, jobs) and ranks articles by Wikipedia pageviews so you can focus on the most-viewed articles first. When you find an article that needs a better image, you can search Openverse directly from the review interface.

## Contributing

Found a good replacement image? The actual Wikipedia edit happens on Wikipedia itself - this tool helps you find opportunities and track your progress.

## License

MIT
