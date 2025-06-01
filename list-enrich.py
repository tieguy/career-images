#!/usr/bin/env python3
"""
list-enrich.py - Processes and enriches career data

This script reads the data produced by list-fetcher.py and provides various
processing and output options. By default, it displays the top 20 most-visited
career articles.
"""

import json
import csv
import sys
from datetime import datetime

def load_data(filename='careers_data.json'):
    """
    Load career data from JSON file

    Args:
        filename (str): Input filename

    Returns:
        dict: Data dictionary with metadata and careers
    """
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"Loaded {len(data['careers'])} careers from {filename}")
        print(f"Data created: {data['metadata']['created']}")
        return data
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found. Please run list-fetcher.py first.")
        return None
    except Exception as e:
        print(f"Error loading data: {e}")
        return None

def sort_by_pageviews(careers, key='avg_daily_views'):
    """
    Sort careers by pageview metrics

    Args:
        careers (list): List of career dictionaries
        key (str): Sort key (total_views_2024, avg_monthly_views, avg_daily_views)

    Returns:
        list: Sorted list of careers
    """
    return sorted(careers, key=lambda x: x.get(key, 0), reverse=True)

def display_top_careers(careers, n=20):
    """
    Display the top N most-visited career articles

    Args:
        careers (list): List of career dictionaries
        n (int): Number of careers to display
    """
    print(f"\nTop {n} Most-Visited Career Articles in 2024")
    print("=" * 80)
    print(f"{'Rank':<5} {'Career':<35} {'Total Views':>12} {'Daily Avg':>10}")
    print("-" * 80)

    for i, career in enumerate(careers[:n], 1):
        total_views = f"{career['total_views_2024']:,}"
        daily_avg = f"{career['avg_daily_views']:,.1f}"
        career_name = career['career_name'][:34]  # Truncate if too long

        print(f"{i:<5} {career_name:<35} {total_views:>12} {daily_avg:>10}")

    print("=" * 80)

def save_to_csv(careers, filename='careers_ranked.csv'):
    """
    Save careers data to CSV file

    Args:
        careers (list): List of career dictionaries
        filename (str): Output filename
    """
    fieldnames = [
        'rank', 'career_name', 'wikipedia_title', 'total_views_2024', 
        'avg_monthly_views', 'avg_daily_views', 'months_counted', 
        'wikipedia_url', 'wikidata_item'
    ]

    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i, career in enumerate(careers, 1):
            # Create output row without the encoded title
            row = {k: v for k, v in career.items() if k != 'wikipedia_title_encoded'}
            row['rank'] = i
            writer.writerow(row)

    print(f"Saved ranked careers to {filename}")

def get_statistics(careers):
    """
    Calculate and display statistics about the career data

    Args:
        careers (list): List of career dictionaries
    """
    total_careers = len(careers)
    total_views = sum(c['total_views_2024'] for c in careers)
    avg_views = total_views / total_careers if total_careers > 0 else 0

    # Find careers with no views
    no_views = [c for c in careers if c['total_views_2024'] == 0]

    print("\nDataset Statistics")
    print("=" * 50)
    print(f"Total careers: {total_careers:,}")
    print(f"Total pageviews in 2024: {total_views:,}")
    print(f"Average views per career: {avg_views:,.1f}")
    print(f"Careers with no recorded views: {len(no_views)}")

    if careers:
        top_career = careers[0]
        print(f"\nMost viewed: {top_career['career_name']} ({top_career['total_views_2024']:,} views)")

        # Find median
        sorted_views = sorted(c['total_views_2024'] for c in careers)
        median_views = sorted_views[len(sorted_views) // 2]
        print(f"Median views: {median_views:,}")

def search_career(careers, search_term):
    """
    Search for careers matching a search term

    Args:
        careers (list): List of career dictionaries
        search_term (str): Term to search for

    Returns:
        list: Matching careers
    """
    search_lower = search_term.lower()
    matches = [c for c in careers if search_lower in c['career_name'].lower()]

    if matches:
        print(f"\nFound {len(matches)} careers matching '{search_term}':")
        for career in matches[:10]:  # Show max 10 results
            print(f"- {career['career_name']}: {career['total_views_2024']:,} views")
        if len(matches) > 10:
            print(f"  ... and {len(matches) - 10} more")
    else:
        print(f"\nNo careers found matching '{search_term}'")

    return matches

def main():
    """Main execution function"""
    # Parse command line arguments
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
    else:
        command = 'top'  # Default command

    # Load data
    data = load_data('careers_data.json')
    if not data:
        return 1

    careers = data['careers']

    # Sort by daily average views (default)
    careers_sorted = sort_by_pageviews(careers, 'avg_daily_views')

    # Execute command
    if command == 'top':
        # Default: show top 20
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        display_top_careers(careers_sorted, n)

    elif command == 'stats':
        # Show statistics
        get_statistics(careers_sorted)

    elif command == 'csv':
        # Export to CSV
        filename = sys.argv[2] if len(sys.argv) > 2 else 'careers_ranked.csv'
        save_to_csv(careers_sorted, filename)

    elif command == 'search':
        # Search for careers
        if len(sys.argv) < 3:
            print("Usage: python list-enrich.py search <search_term>")
            return 1
        search_term = ' '.join(sys.argv[2:])
        search_career(careers_sorted, search_term)

    elif command == 'bottom':
        # Show bottom careers
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        print(f"\nBottom {n} Least-Visited Career Articles in 2024")
        print("=" * 80)
        print(f"{'Rank':<5} {'Career':<35} {'Total Views':>12} {'Daily Avg':>10}")
        print("-" * 80)

        start_idx = len(careers_sorted) - n
        for i, career in enumerate(careers_sorted[-n:], start_idx + 1):
            total_views = f"{career['total_views_2024']:,}"
            daily_avg = f"{career['avg_daily_views']:,.1f}"
            career_name = career['career_name'][:34]
            print(f"{i:<5} {career_name:<35} {total_views:>12} {daily_avg:>10}")

        print("=" * 80)

    else:
        print(f"Unknown command: {command}")
        print("\nUsage: python list-enrich.py [command] [options]")
        print("\nCommands:")
        print("  top [n]           - Show top N careers (default: 20)")
        print("  bottom [n]        - Show bottom N careers (default: 20)")
        print("  stats             - Show dataset statistics")
        print("  csv [filename]    - Export to CSV file")
        print("  search <term>     - Search for careers")
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())