#!/usr/bin/env python3
"""
fetcher.py - Fetches career data from Wikidata and Wikipedia pageview metrics

Uses P106 (occupation) property values as the source of professions, filtered by
P31 (instance of) to legitimate profession-related classes. This approach:
1. Only includes items actually used as occupations (no garbage like places)
2. Fast batched queries that don't timeout
3. Better coverage of legitimate professions

Commands:
    fetch              Fetch all careers from Wikidata and their pageviews
    fetch --limit N    Fetch only N careers (for testing)
    resume             Continue fetching pageviews for careers that don't have them
    stats              Show dataset statistics
    top N              Show top N careers by pageviews
"""

import asyncio
import aiohttp
import json
import os
import requests
import sys
import time
from datetime import datetime

from db import get_database

# Path to cached career classes
CAREER_CLASSES_FILE = os.path.join(os.path.dirname(__file__), 'career_classes.json')


def log(message: str, level: str = "INFO"):
    """Print timestamped log message"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {level}: {message}")


def load_career_classes() -> set[str]:
    """Load career classes from cache, including base classes and additional types."""
    try:
        with open(CAREER_CLASSES_FILE) as f:
            data = json.load(f)

        # Combine all class sources
        classes = set(data.get('classes', []))
        classes.update(data.get('base_classes', []))

        # Additional profession-related meta-types
        additional = {
            'Q486983',   # academic rank (professor, etc.)
            'Q355567',   # noble title
            'Q480319',   # title of authority
            'Q627436',   # field of work
            'Q5767753',  # style
        }
        classes.update(additional)

        return classes

    except FileNotFoundError:
        log(f"Career classes cache not found: {CAREER_CLASSES_FILE}", "WARNING")
        # Fallback to base classes + additional
        return {
            'Q28640', 'Q12737077', 'Q192581', 'Q4164871', 'Q136649946',
            'Q486983', 'Q355567', 'Q480319', 'Q627436', 'Q5767753',
        }


def query_p106_occupations(career_classes: set[str], batch_size: int = 30) -> list[str]:
    """
    Query Wikidata for all P106 occupation values that have P31 to our career classes.
    Returns list of Wikidata Q-IDs.
    """
    log(f"Querying P106 occupations with P31 to {len(career_classes)} career classes...")

    url = 'https://query.wikidata.org/sparql'
    headers = {
        'User-Agent': 'WikipediaCareerDiversityTool/1.0',
        'Accept': 'application/sparql-results+json',
    }

    all_occupations = set()
    classes_list = list(career_classes)

    for i in range(0, len(classes_list), batch_size):
        batch = classes_list[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(classes_list) + batch_size - 1) // batch_size

        values = ' '.join(f'wd:{c}' for c in batch)
        query = f'''SELECT DISTINCT ?occupation WHERE {{
          VALUES ?careerClass {{ {values} }}
          ?person wdt:P106 ?occupation .
          ?occupation wdt:P31 ?careerClass .
        }}'''

        try:
            r = requests.post(url, data={'query': query}, headers=headers, timeout=120)
            r.raise_for_status()
            bindings = r.json()['results']['bindings']

            for b in bindings:
                qid = b['occupation']['value'].split('/')[-1]
                all_occupations.add(qid)

            log(f"  Batch {batch_num}/{total_batches}: {len(bindings)} results, total: {len(all_occupations)}")

        except requests.RequestException as e:
            log(f"  Batch {batch_num} failed: {e}", "WARNING")

        time.sleep(0.5)  # Rate limiting

    log(f"Found {len(all_occupations)} unique P106 occupations")
    return list(all_occupations)


def fetch_occupation_details(occupation_ids: list[str], batch_size: int = 100) -> list[dict]:
    """
    Fetch details for occupation items: labels, Wikipedia URLs.
    Filters to only items with English Wikipedia articles.
    """
    log(f"Fetching details for {len(occupation_ids)} occupations...")

    url = 'https://query.wikidata.org/sparql'
    headers = {
        'User-Agent': 'WikipediaCareerDiversityTool/1.0',
        'Accept': 'application/sparql-results+json',
    }

    careers = []

    for i in range(0, len(occupation_ids), batch_size):
        batch = occupation_ids[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(occupation_ids) + batch_size - 1) // batch_size

        values = ' '.join(f'wd:{qid}' for qid in batch)
        query = f'''
        SELECT ?occupation ?occupationLabel ?article ?typeId WHERE {{
          VALUES ?occupation {{ {values} }}

          # Must have English Wikipedia article
          ?article schema:about ?occupation ;
                   schema:isPartOf <https://en.wikipedia.org/> .

          # Get one P31 type for categorization
          OPTIONAL {{ ?occupation wdt:P31 ?typeId }}

          SERVICE wikibase:label {{
            bd:serviceParam wikibase:language "en".
            ?occupation rdfs:label ?occupationLabel .
          }}
          FILTER(LANG(?occupationLabel) = "en")
        }}
        '''

        try:
            r = requests.post(url, data={'query': query}, headers=headers, timeout=120)
            r.raise_for_status()
            bindings = r.json()['results']['bindings']

            # Deduplicate (multiple types per item)
            seen = set()
            for b in bindings:
                qid = b['occupation']['value'].split('/')[-1]
                if qid in seen:
                    continue
                seen.add(qid)

                name = b['occupationLabel']['value']
                if name.startswith('Q'):  # No English label
                    continue

                wikipedia_url = b['article']['value']
                type_id = b.get('typeId', {}).get('value', '').split('/')[-1] or None

                careers.append({
                    'wikidata_id': qid,
                    'name': name,
                    'category': get_category_from_type(type_id),
                    'wikipedia_url': wikipedia_url,
                })

            log(f"  Batch {batch_num}/{total_batches}: {len(seen)} with Wikipedia articles")

        except requests.RequestException as e:
            log(f"  Batch {batch_num} failed: {e}", "WARNING")

        time.sleep(0.5)

    log(f"Found {len(careers)} occupations with English Wikipedia articles")
    return careers


def get_category_from_type(type_id: str) -> str:
    """Map a Wikidata type Q-ID to a category name (must be in DB allowed values)."""
    base_map = {
        'Q28640': 'profession',
        'Q12737077': 'occupation',
        'Q192581': 'job',
        'Q4164871': 'position',
        'Q136649946': 'position',
        'Q486983': 'position',   # academic rank → position
        'Q355567': 'position',   # noble title → position
        'Q480319': 'position',   # title of authority → position
        'Q627436': 'occupation', # field of work → occupation
        'Q5767753': 'position',  # style → position
    }
    return base_map.get(type_id, 'profession')


def extract_title_from_url(url: str) -> str:
    """Extract Wikipedia article title from URL"""
    return url.split('/wiki/')[-1] if '/wiki/' in url else ''


async def fetch_pageviews(session: aiohttp.ClientSession, title: str) -> tuple[int, float]:
    """Fetch pageview data for a Wikipedia article for 2024+2025."""
    url = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{title}/monthly/2024010100/2025123100"
    headers = {'User-Agent': 'WikipediaCareerDiversityTool/1.0'}

    try:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                items = data.get('items', [])
                total_views = sum(item['views'] for item in items)
                days = len(items) * 30.44
                avg_daily = total_views / days if days > 0 else 0
                return (total_views, round(avg_daily, 2))
            return (0, 0.0)
    except Exception:
        return (0, 0.0)


async def fetch_pageviews_batch(careers: list[dict], concurrency: int = 50) -> list[tuple[str, int, float]]:
    """Fetch pageviews for a batch of careers concurrently."""
    semaphore = asyncio.Semaphore(concurrency)

    async def fetch_one(career: dict, session: aiohttp.ClientSession) -> tuple[str, int, float]:
        async with semaphore:
            title = extract_title_from_url(career['wikipedia_url'])
            total, avg = await fetch_pageviews(session, title)
            return (career['wikidata_id'], total, avg)

    connector = aiohttp.TCPConnector(limit=concurrency)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [fetch_one(c, session) for c in careers]
        total = len(tasks)
        results = []

        chunk_size = 500
        for i in range(0, total, chunk_size):
            chunk = tasks[i:i + chunk_size]
            chunk_results = await asyncio.gather(*chunk)
            results.extend(chunk_results)

            progress = min(i + chunk_size, total)
            log(f"Pageviews: {progress}/{total} ({progress * 100 // total}%)")

    return results


def cmd_fetch(limit: int = None):
    """Fetch careers using P106-based approach."""
    db = get_database()
    db.init_schema()

    # Step 1: Load career classes
    career_classes = load_career_classes()
    log(f"Using {len(career_classes)} career classes")

    # Step 2: Get P106 occupation Q-IDs
    occupation_ids = query_p106_occupations(career_classes)
    if limit:
        occupation_ids = occupation_ids[:limit]

    # Step 3: Fetch details (with Wikipedia filter)
    careers = fetch_occupation_details(occupation_ids)
    if not careers:
        log("No careers found", "ERROR")
        return 1

    # Step 4: Store in database
    log(f"Storing {len(careers)} careers in database...")
    db.upsert_careers(careers)

    # Step 5: Fetch pageviews
    log("Fetching pageviews...")
    start_time = datetime.now()
    results = asyncio.run(fetch_pageviews_batch(careers))
    elapsed = (datetime.now() - start_time).total_seconds()
    log(f"Fetched pageviews in {elapsed:.1f} seconds")

    # Step 6: Update database
    db.update_pageviews_batch(results)

    # Summary
    stats = db.get_stats()
    log(f"Done! {stats['total_careers']} careers, {stats['with_pageviews']} with pageviews")
    return 0


def cmd_resume():
    """Continue fetching pageviews for careers that don't have them."""
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
    """Show dataset statistics."""
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
    """Show top N careers by pageviews."""
    db = get_database()
    careers = db.get_top_careers(limit=n)

    print(f"\nTop {n} Careers by Daily Views")
    print("=" * 80)
    print(f"{'Rank':<5} {'Career':<40} {'Daily Views':>12} {'Status':<12}")
    print("-" * 80)

    for i, career in enumerate(careers, 1):
        name = career['name'][:38]
        avg = career['avg_daily_views']
        status = career['status']
        print(f"{i:<5} {name:<40} {avg:>12,.0f} {status:<12}")

    return 0


def main():
    """Main CLI entry point."""
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

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
