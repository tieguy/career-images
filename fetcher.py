#!/usr/bin/env python3
"""
fetcher.py - Fetches career data from Wikidata and Wikipedia pageview metrics

Commands:
    fetch              Fetch all careers from Wikidata and their pageviews
    fetch --limit N    Fetch only N careers (for testing)
    resume             Continue fetching pageviews for careers that don't have them
    stats              Show dataset statistics
    top N              Show top N careers by pageviews
    refresh-classes    Regenerate career_classes.json from Wikidata
"""

import asyncio
import aiohttp
import json
import os
import requests
import sys
import urllib.parse
from datetime import datetime

from db import get_database, CATEGORY_MAP

# Path to cached career classes
CAREER_CLASSES_FILE = os.path.join(os.path.dirname(__file__), 'career_classes.json')


def load_career_classes() -> list[str]:
    """Load pre-computed career classes from cache file."""
    try:
        with open(CAREER_CLASSES_FILE) as f:
            data = json.load(f)
        return data['classes']
    except FileNotFoundError:
        log(f"Career classes cache not found: {CAREER_CLASSES_FILE}", "WARNING")
        log("Run 'fetcher.py refresh-classes' to generate it", "WARNING")
        # Fallback to base classes
        return ['Q28640', 'Q12737077', 'Q192581', 'Q4164871', 'Q136649946']


def log(message: str, level: str = "INFO"):
    """Print timestamped log message"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {level}: {message}")


def query_wikidata_careers(limit: int = None) -> list[dict]:
    """
    Query Wikidata for articles about careers/occupations.
    Returns list of career dicts with wikidata_id, name, category, wikipedia_url.
    Batches queries to avoid timeouts with large class lists.
    """
    log("Querying Wikidata for career articles...")

    # Load pre-computed career classes from cache
    career_classes = load_career_classes()
    log(f"Using {len(career_classes)} career classes from cache")

    url = 'https://query.wikidata.org/sparql'
    headers = {
        'User-Agent': 'WikipediaCareerDiversityTool/1.0 (https://github.com/tieguy/wikipedia-career-images)',
        'Accept': 'application/sparql-results+json',
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    # Batch classes to avoid query timeouts (50 classes per batch works reliably)
    BATCH_SIZE = 50
    all_bindings = []

    for i in range(0, len(career_classes), BATCH_SIZE):
        batch = career_classes[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(career_classes) + BATCH_SIZE - 1) // BATCH_SIZE
        log(f"Querying batch {batch_num}/{total_batches} ({len(batch)} classes)...")

        values_clause = " ".join(f"wd:{qid}" for qid in batch)

        query = f"""
        SELECT DISTINCT ?item ?itemLabel ?categoryId ?article WHERE {{
          VALUES ?categoryId {{ {values_clause} }}

          ?item wdt:P31 ?categoryId .
          ?article schema:about ?item ;
                   schema:isPartOf <https://en.wikipedia.org/> .
          SERVICE wikibase:label {{
            bd:serviceParam wikibase:language "en".
            ?item rdfs:label ?itemLabel .
          }}
          FILTER(LANG(?itemLabel) = "en")
        }}
        """

        try:
            response = requests.post(url, data={'query': query}, headers=headers, timeout=120)
            response.raise_for_status()
            results = response.json()
            bindings = results.get('results', {}).get('bindings', [])
            all_bindings.extend(bindings)
            log(f"  Got {len(bindings)} results")
        except requests.RequestException as e:
            log(f"  Error in batch {batch_num}: {e}", "WARNING")
            continue

        # Early exit if we have enough for limit
        if limit and len(all_bindings) >= limit:
            break

    log(f"Total: {len(all_bindings)} results from Wikidata")

    # Parse results into career dicts, deduplicating across batches
    careers = []
    seen = set()

    for binding in all_bindings:
        wikidata_url = binding.get('item', {}).get('value', '')
        wikidata_id = wikidata_url.split('/')[-1] if wikidata_url else None

        if wikidata_id in seen:
            continue
        seen.add(wikidata_id)

        name = binding.get('itemLabel', {}).get('value', '')
        if not name or name.startswith('Q'):  # Skip if no proper label
            continue

        category_url = binding.get('categoryId', {}).get('value', '')
        category_id = category_url.split('/')[-1] if category_url else None
        category = CATEGORY_MAP.get(category_id)

        wikipedia_url = binding.get('article', {}).get('value', '')

        if wikidata_id and wikipedia_url:
            careers.append({
                'wikidata_id': wikidata_id,
                'name': name,
                'category': category,
                'wikipedia_url': wikipedia_url,
            })

        # Apply limit after deduplication
        if limit and len(careers) >= limit:
            break

    log(f"Parsed {len(careers)} valid careers")
    return careers


def extract_title_from_url(url: str) -> str:
    """Extract Wikipedia article title from URL, properly encoded for API calls"""
    # URL is like https://en.wikipedia.org/wiki/Software_engineer
    title = url.split('/wiki/')[-1] if '/wiki/' in url else ''
    return title


async def fetch_pageviews(session: aiohttp.ClientSession, title: str) -> tuple[int, float]:
    """
    Fetch pageview data for a Wikipedia article for 2024+2025.
    Returns (total_views, avg_daily_views).
    """
    # Fetch from Jan 2024 to Dec 2025
    url = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{title}/monthly/2024010100/2025123100"

    headers = {
        'User-Agent': 'WikipediaCareerDiversityTool/1.0'
    }

    try:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                items = data.get('items', [])
                total_views = sum(item['views'] for item in items)
                # Calculate avg daily views (approximate days in the period)
                days = len(items) * 30.44  # Average days per month
                avg_daily = total_views / days if days > 0 else 0
                return (total_views, round(avg_daily, 2))
            else:
                return (0, 0.0)
    except Exception:
        return (0, 0.0)


async def fetch_pageviews_batch(careers: list[dict], concurrency: int = 50) -> list[tuple[str, int, float]]:
    """
    Fetch pageviews for a batch of careers concurrently.
    Returns list of (wikidata_id, total_views, avg_daily_views).
    """
    semaphore = asyncio.Semaphore(concurrency)
    results = []

    async def fetch_one(career: dict) -> tuple[str, int, float]:
        async with semaphore:
            title = extract_title_from_url(career['wikipedia_url'])
            total, avg = await fetch_pageviews(session, title)
            return (career['wikidata_id'], total, avg)

    connector = aiohttp.TCPConnector(limit=concurrency)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [fetch_one(c) for c in careers]
        total = len(tasks)

        # Process in chunks for progress reporting
        chunk_size = 500
        for i in range(0, total, chunk_size):
            chunk = tasks[i:i + chunk_size]
            chunk_results = await asyncio.gather(*chunk)
            results.extend(chunk_results)

            progress = min(i + chunk_size, total)
            log(f"Pageviews: {progress}/{total} ({progress * 100 // total}%)")

    return results


def cmd_fetch(limit: int = None):
    """Fetch careers from Wikidata and their pageviews"""
    db = get_database()
    db.init_schema()

    # Step 1: Query Wikidata
    careers = query_wikidata_careers(limit=limit)
    if not careers:
        log("No careers found", "ERROR")
        return 1

    # Step 2: Store careers in database
    log(f"Storing {len(careers)} careers in database...")
    db.upsert_careers(careers)

    # Step 3: Fetch pageviews
    log("Fetching pageviews (this may take a few minutes)...")
    start_time = datetime.now()

    results = asyncio.run(fetch_pageviews_batch(careers))

    elapsed = (datetime.now() - start_time).total_seconds()
    log(f"Fetched pageviews in {elapsed:.1f} seconds")

    # Step 4: Update database with pageviews
    log("Updating database with pageview data...")
    db.update_pageviews_batch(results)

    # Summary
    stats = db.get_stats()
    log(f"Done! {stats['total_careers']} careers, {stats['with_pageviews']} with pageviews")
    log(f"Total pageviews: {stats['total_views']:,}")
    if stats.get('top_career'):
        log(f"Top career: {stats['top_career']['name']} ({stats['top_career']['views']:,} views)")

    return 0


def cmd_resume():
    """Continue fetching pageviews for careers that don't have them"""
    db = get_database()

    careers = db.get_careers_needing_pageviews()
    if not careers:
        log("All careers have pageview data")
        return 0

    log(f"Found {len(careers)} careers needing pageviews")

    results = asyncio.run(fetch_pageviews_batch(careers))
    db.update_pageviews_batch(results)

    log(f"Updated {len(results)} careers")
    return 0


def cmd_stats():
    """Show dataset statistics"""
    db = get_database()
    stats = db.get_stats()

    print(f"\nDataset Statistics")
    print("=" * 50)
    print(f"Total careers: {stats['total_careers']:,}")
    print(f"With pageviews: {stats['with_pageviews']:,}")
    print(f"Total pageviews: {stats['total_views']:,}")

    if stats.get('by_category'):
        print(f"\nBy category:")
        for cat, count in sorted(stats['by_category'].items(), key=lambda x: -x[1]):
            print(f"  {cat or 'unknown'}: {count:,}")

    if stats.get('by_status'):
        print(f"\nBy status:")
        for status, count in sorted(stats['by_status'].items(), key=lambda x: -x[1]):
            print(f"  {status}: {count:,}")

    if stats.get('top_career'):
        print(f"\nTop career: {stats['top_career']['name']} ({stats['top_career']['views']:,} views)")

    return 0


def cmd_top(n: int = 20):
    """Show top N careers by pageviews"""
    db = get_database()
    careers = db.get_top_careers(limit=n)

    print(f"\nTop {n} Careers by Pageviews")
    print("=" * 80)
    print(f"{'Rank':<5} {'Career':<40} {'Daily Avg':>12} {'Status':<12}")
    print("-" * 80)

    for i, career in enumerate(careers, 1):
        name = career['name'][:38]
        avg = career['avg_daily_views']
        status = career['status']
        print(f"{i:<5} {name:<40} {avg:>12,.0f} {status:<12}")

    return 0


def cmd_refresh_classes():
    """Regenerate career_classes.json from Wikidata"""
    log("Querying Wikidata for all career-related classes...")
    log("This queries subclasses of profession, occupation, job, position")
    log("Excluding: automobile manufacturers, cities...")

    query = """
    SELECT DISTINCT ?class WHERE {
      {
        ?class wdt:P279* wd:Q28640 .
      } UNION {
        ?class wdt:P279* wd:Q12737077 .
      } UNION {
        ?class wdt:P279* wd:Q192581 .
      } UNION {
        ?class wdt:P279* wd:Q4164871 .
      } UNION {
        ?class wdt:P279* wd:Q136649946 .
      }
      # Exclude noisy hierarchies
      FILTER NOT EXISTS { ?class wdt:P279* wd:Q786820 . }  # automobile manufacturer
      FILTER NOT EXISTS { ?class wdt:P279* wd:Q515 . }     # city
      FILTER EXISTS {
        ?item wdt:P31 ?class .
        ?article schema:about ?item ;
                 schema:isPartOf <https://en.wikipedia.org/> .
      }
    }
    """

    url = 'https://query.wikidata.org/sparql'
    headers = {
        'User-Agent': 'WikipediaCareerDiversityTool/1.0',
        'Accept': 'application/sparql-results+json'
    }

    try:
        response = requests.get(url, params={'query': query}, headers=headers, timeout=300)
        response.raise_for_status()
        results = response.json()
    except requests.RequestException as e:
        log(f"Error querying Wikidata: {e}", "ERROR")
        return 1

    bindings = results.get('results', {}).get('bindings', [])
    classes = sorted([r['class']['value'].split('/')[-1] for r in bindings])

    log(f"Found {len(classes)} career-related classes")

    cache = {
        'generated': datetime.now().isoformat(),
        'description': 'Pre-computed Wikidata classes for career/occupation/profession articles',
        'excluded': ['Q786820'],
        'base_classes': ['Q28640', 'Q12737077', 'Q192581', 'Q4164871', 'Q136649946'],
        'classes': classes
    }

    with open(CAREER_CLASSES_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

    log(f"Saved to {CAREER_CLASSES_FILE}")
    return 0


def main():
    """Main CLI entry point"""
    args = sys.argv[1:]

    if not args or args[0] == 'help':
        print(__doc__)
        return 0

    cmd = args[0]

    if cmd == 'fetch':
        limit = None
        if '--limit' in args:
            idx = args.index('--limit')
            if idx + 1 < len(args):
                limit = int(args[idx + 1])
        return cmd_fetch(limit=limit)

    elif cmd == 'resume':
        return cmd_resume()

    elif cmd == 'stats':
        return cmd_stats()

    elif cmd == 'top':
        n = int(args[1]) if len(args) > 1 else 20
        return cmd_top(n)

    elif cmd == 'refresh-classes':
        return cmd_refresh_classes()

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
