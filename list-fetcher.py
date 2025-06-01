#!/usr/bin/env python3
"""
list-fetcher.py - Fetches career data from Wikidata and Wikipedia pageview metrics

This script queries Wikidata for career/occupation articles and fetches their
2024 pageview statistics from Wikipedia. The data is saved to a JSON file for
later processing.
"""

import requests
import json
import time
from datetime import datetime
import sys

def log(message, level="INFO"):
    """Print timestamped log message"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {level}: {message}")

def query_wikidata_careers(limit=None):
    """
    Query Wikidata for articles about careers/occupations

    Args:
        limit (int): Optional limit for development/testing

    Returns:
        dict: SPARQL query results
    """
    log("Preparing SPARQL query for career articles...")

    # SPARQL query to find occupation/profession articles
    query = """
    SELECT DISTINCT ?item ?itemLabel ?article WHERE {
      # More targeted approach - direct instances only
      {
        ?item wdt:P31 wd:Q28640 .  # Direct instances of profession
      } UNION {
        ?item wdt:P31 wd:Q12737077 .  # Direct instances of occupation  
      } UNION {
        ?item wdt:P31 wd:Q192581 .  # job (like plumber, electrician)
      } UNION {
        ?item wdt:P31 wd:Q4164871 .  # position (CEO, prime minister, etc.)
      }

      # Must have English Wikipedia article
      ?article schema:about ?item ;
               schema:isPartOf <https://en.wikipedia.org/> .

      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    ORDER BY ?itemLabel
    """

    # Add LIMIT for development mode
    if limit:
        query += f"\nLIMIT {limit}"
        log(f"Query limited to {limit} results for development")

    url = 'https://query.wikidata.org/sparql'
    headers = {
        'User-Agent': 'Career-Research-Script/1.0',
        'Accept': 'application/sparql-results+json'
    }

    log(f"Sending query to Wikidata SPARQL endpoint...")
    try:
        response = requests.get(url, params={'query': query}, headers=headers)

        if response.status_code == 200:
            results = response.json()
            count = len(results['results']['bindings'])
            log(f"Successfully retrieved {count} results from Wikidata")
            return results
        else:
            log(f"Error querying Wikidata: HTTP {response.status_code}", "ERROR")
            log(f"Response: {response.text}", "ERROR")
            return None
    except Exception as e:
        log(f"Exception during Wikidata query: {e}", "ERROR")
        return None

def extract_wikipedia_titles(sparql_results):
    """
    Extract Wikipedia article titles and metadata from SPARQL results

    Args:
        sparql_results (dict): SPARQL query results

    Returns:
        list: List of career dictionaries
    """
    careers = []

    if not sparql_results:
        return careers

    bindings = sparql_results['results']['bindings']
    log(f"Extracting Wikipedia titles from {len(bindings)} SPARQL results...")

    for i, result in enumerate(bindings):
        if i % 500 == 0:
            log(f"Processing result {i}/{len(bindings)}...")

        # Extract values from SPARQL result
        wikidata_item = result.get('item', {}).get('value', '')
        career_name = result.get('itemLabel', {}).get('value', 'Unknown')
        wiki_url = result.get('article', {}).get('value', '')

        # Extract title from Wikipedia URL
        if wiki_url:
            # Keep encoded format for API calls
            wiki_title_encoded = wiki_url.split('/')[-1]
            # Create readable version
            wiki_title_readable = wiki_title_encoded.replace('_', ' ')
            try:
                import urllib.parse
                wiki_title_readable = urllib.parse.unquote(wiki_title_readable)
            except:
                pass
        else:
            wiki_title_encoded = 'Unknown'
            wiki_title_readable = 'Unknown'

        careers.append({
            'wikidata_item': wikidata_item,
            'career_name': career_name,
            'wikipedia_url': wiki_url,
            'wikipedia_title': wiki_title_readable,
            'wikipedia_title_encoded': wiki_title_encoded
        })

    log(f"Successfully extracted {len(careers)} career entries")
    return careers

def get_pageview_data_2024(article_title_encoded):
    """
    Get pageview data for a Wikipedia article for all of 2024 using monthly data

    Args:
        article_title_encoded (str): Wikipedia article title (URL-encoded)

    Returns:
        dict: Pageview statistics or None if error
    """
    # Get all of 2024 (January through December)
    start_str = '2024010100'  # January 1, 2024
    end_str = '2024123100'    # December 31, 2024

    url = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{article_title_encoded}/monthly/{start_str}/{end_str}"

    headers = {
        'User-Agent': 'Career-Research-Script/1.0'
    }

    try:
        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()

            # Calculate statistics
            total_views = sum(item['views'] for item in data['items'])
            avg_monthly_views = total_views / len(data['items']) if data['items'] else 0
            avg_daily_views = avg_monthly_views / 30.44  # Average days per month

            return {
                'total_views_2024': total_views,
                'avg_monthly_views': round(avg_monthly_views, 2),
                'avg_daily_views': round(avg_daily_views, 2),
                'months_counted': len(data['items'])
            }
        else:
            return None

    except Exception as e:
        return None

def add_pageview_data(careers, delay=0.1):
    """
    Add 2024 pageview data to careers list

    Args:
        careers (list): List of career dictionaries
        delay (float): Delay between API calls

    Returns:
        list: Updated careers list with pageview data
    """
    total = len(careers)
    log(f"Starting pageview data collection for {total} articles...")
    log(f"Estimated time: {total * delay / 60:.1f} minutes")

    successful_calls = 0
    failed_calls = 0
    start_time = time.time()

    for i, career in enumerate(careers):
        # Progress logging
        if i % 100 == 0 and i > 0:
            elapsed = time.time() - start_time
            rate = i / elapsed
            remaining = (total - i) / rate
            log(f"Progress: {i}/{total} ({i/total*100:.1f}%) - "
                f"Success: {successful_calls}, Failed: {failed_calls} - "
                f"ETA: {remaining/60:.1f} minutes")

        # Get pageview data
        pageview_data = get_pageview_data_2024(career['wikipedia_title_encoded'])

        if pageview_data:
            career.update(pageview_data)
            successful_calls += 1
        else:
            # Set defaults if pageview data unavailable
            career.update({
                'total_views_2024': 0,
                'avg_monthly_views': 0,
                'avg_daily_views': 0,
                'months_counted': 0
            })
            failed_calls += 1

        # Rate limiting
        time.sleep(delay)

    elapsed_total = time.time() - start_time
    log(f"Pageview data collection complete in {elapsed_total/60:.1f} minutes")
    log(f"Success rate: {successful_calls}/{total} ({successful_calls/total*100:.1f}%)")

    return careers

def save_data(careers, filename='careers_data.json'):
    """
    Save careers data to JSON file

    Args:
        careers (list): List of career dictionaries
        filename (str): Output filename
    """
    log(f"Saving data to {filename}...")

    # Add metadata
    data = {
        'metadata': {
            'created': datetime.now().isoformat(),
            'total_careers': len(careers),
            'source': 'Wikidata and Wikipedia Pageview API',
            'year': 2024
        },
        'careers': careers
    }

    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log(f"Successfully saved {len(careers)} career entries to {filename}")
    except Exception as e:
        log(f"Error saving data: {e}", "ERROR")
        raise

def main():
    """Main execution function"""
    log("=== Career Data Fetcher ===")
    log("Starting data collection process...")

    # Parse command line arguments
    limit = None
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
            log(f"Running in development mode with limit: {limit}")
        except ValueError:
            log("Invalid limit argument, running without limit", "WARNING")

    # Step 1: Query Wikidata
    log("Step 1: Querying Wikidata for career articles...")
    results = query_wikidata_careers(limit=limit)

    if not results:
        log("Failed to retrieve data from Wikidata", "ERROR")
        return 1

    # Step 2: Extract Wikipedia titles
    log("Step 2: Extracting Wikipedia article information...")
    careers = extract_wikipedia_titles(results)

    if not careers:
        log("No careers extracted from results", "ERROR")
        return 1

    log(f"Found {len(careers)} career articles")

    # Step 3: Add pageview data
    log("Step 3: Fetching 2024 pageview data from Wikipedia API...")
    careers_with_views = add_pageview_data(careers, delay=0.1)

    # Step 4: Save results
    log("Step 4: Saving results...")
    save_data(careers_with_views, 'careers_data.json')

    log("=== Data fetching complete! ===")
    log(f"Total careers processed: {len(careers_with_views)}")
    log("Output saved to: careers_data.json")

    return 0

if __name__ == "__main__":
    sys.exit(main())