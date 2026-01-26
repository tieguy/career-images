#!/usr/bin/env python3
"""
audit.py - Audit tool for tracking uploaded images in Wikipedia articles.

This tool checks whether images that were uploaded to Commons and added to
Wikipedia articles are still present in those articles.

Usage:
    uv run python audit.py add <article> <filename> [--notes NOTES]
    uv run python audit.py check [--all | <article>]
    uv run python audit.py list [--status STATUS]
    uv run python audit.py stats
"""

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import requests

DB_PATH = "audit.db"

HEADERS = {
    'User-Agent': 'WikipediaCareerDiversityTool/1.0 (https://github.com/tieguy/wikipedia-career-images)'
}


def get_connection():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the audit database."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS uploaded_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_title TEXT NOT NULL,
            filename TEXT NOT NULL,
            notes TEXT,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_checked TEXT,
            status TEXT DEFAULT 'unknown'
                CHECK(status IN ('unknown', 'present', 'removed', 'error')),
            removal_detected_at TEXT,
            UNIQUE(article_title, filename)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_article ON uploaded_images(article_title)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON uploaded_images(status)")
    conn.commit()
    conn.close()


def add_image(article_title: str, filename: str, notes: str = None):
    """Add an image to the tracking database."""
    conn = get_connection()

    # Normalize filename (remove File: prefix if present)
    if filename.lower().startswith('file:'):
        filename = filename[5:]

    try:
        conn.execute("""
            INSERT INTO uploaded_images (article_title, filename, notes)
            VALUES (?, ?, ?)
        """, (article_title, filename, notes))
        conn.commit()
        print(f"Added: {filename} → {article_title}")
    except sqlite3.IntegrityError:
        print(f"Already tracking: {filename} in {article_title}")
    finally:
        conn.close()


def get_article_images(article_title: str) -> set:
    """Fetch all images currently in a Wikipedia article."""
    # Use the Wikipedia API to get page content and extract images
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        'action': 'query',
        'titles': article_title,
        'prop': 'images',
        'imlimit': 500,
        'format': 'json',
    }

    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()

        pages = data.get('query', {}).get('pages', {})
        images = set()

        for page in pages.values():
            if page.get('missing'):
                print(f"Warning: Article '{article_title}' not found")
                return set()
            for img in page.get('images', []):
                # Normalize: remove 'File:' prefix and convert to lowercase for comparison
                title = img.get('title', '')
                if title.lower().startswith('file:'):
                    title = title[5:]
                images.add(title.lower())

        return images

    except requests.RequestException as e:
        print(f"Error fetching images for {article_title}: {e}")
        return set()
    except Exception as e:
        print(f"Error parsing response for {article_title}: {e}")
        return set()


def check_image(article_title: str, filename: str) -> str:
    """Check if an image is still in an article. Returns status."""
    images = get_article_images(article_title)

    if not images:
        return 'error'

    # Normalize filename for comparison
    normalized = filename.lower()
    if normalized in images:
        return 'present'
    else:
        return 'removed'


def check_all():
    """Check all tracked images."""
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM uploaded_images")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("No images being tracked. Use 'audit.py add' to add images.")
        return

    print(f"Checking {len(rows)} tracked images...\n")

    results = {'present': 0, 'removed': 0, 'error': 0}
    removals = []

    for row in rows:
        article = row['article_title']
        filename = row['filename']

        status = check_image(article, filename)
        results[status] += 1

        # Update database
        conn = get_connection()
        now = datetime.now().isoformat()

        if status == 'removed' and row['status'] != 'removed':
            conn.execute("""
                UPDATE uploaded_images
                SET status = ?, last_checked = ?, removal_detected_at = ?
                WHERE id = ?
            """, (status, now, now, row['id']))
            removals.append((article, filename))
        else:
            conn.execute("""
                UPDATE uploaded_images
                SET status = ?, last_checked = ?
                WHERE id = ?
            """, (status, now, row['id']))

        conn.commit()
        conn.close()

        # Print status
        symbol = {'present': '✓', 'removed': '✗', 'error': '?'}[status]
        print(f"  {symbol} {filename[:50]} → {article}")

    print(f"\nSummary:")
    print(f"  Present: {results['present']}")
    print(f"  Removed: {results['removed']}")
    print(f"  Errors:  {results['error']}")

    if removals:
        print(f"\nNewly detected removals:")
        for article, filename in removals:
            print(f"  - {filename} from {article}")


def check_article(article_title: str):
    """Check all tracked images for a specific article."""
    conn = get_connection()
    cursor = conn.execute(
        "SELECT * FROM uploaded_images WHERE article_title = ?",
        (article_title,)
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print(f"No tracked images for article: {article_title}")
        return

    print(f"Checking {len(rows)} images for {article_title}...\n")

    for row in rows:
        status = check_image(article_title, row['filename'])
        symbol = {'present': '✓', 'removed': '✗', 'error': '?'}[status]
        print(f"  {symbol} {row['filename']}")

        # Update database
        conn = get_connection()
        now = datetime.now().isoformat()
        if status == 'removed' and row['status'] != 'removed':
            conn.execute("""
                UPDATE uploaded_images
                SET status = ?, last_checked = ?, removal_detected_at = ?
                WHERE id = ?
            """, (status, now, now, row['id']))
        else:
            conn.execute("""
                UPDATE uploaded_images
                SET status = ?, last_checked = ?
                WHERE id = ?
            """, (status, now, row['id']))
        conn.commit()
        conn.close()


def list_images(status_filter: str = None):
    """List tracked images."""
    conn = get_connection()

    if status_filter:
        cursor = conn.execute(
            "SELECT * FROM uploaded_images WHERE status = ? ORDER BY added_at DESC",
            (status_filter,)
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM uploaded_images ORDER BY added_at DESC"
        )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("No images found.")
        return

    print(f"{'Status':<10} {'Article':<30} {'Filename':<40} {'Added':<20}")
    print("-" * 100)

    for row in rows:
        status = row['status']
        article = row['article_title'][:28]
        filename = row['filename'][:38]
        added = row['added_at'][:10] if row['added_at'] else 'unknown'
        print(f"{status:<10} {article:<30} {filename:<40} {added:<20}")


def show_stats():
    """Show statistics about tracked images."""
    conn = get_connection()

    cursor = conn.execute("SELECT COUNT(*) FROM uploaded_images")
    total = cursor.fetchone()[0]

    cursor = conn.execute("""
        SELECT status, COUNT(*) FROM uploaded_images GROUP BY status
    """)
    by_status = {row[0]: row[1] for row in cursor.fetchall()}

    conn.close()

    print("Audit Statistics")
    print("=" * 40)
    print(f"Total tracked images: {total}")
    print()
    print("By status:")
    for status in ['present', 'removed', 'unknown', 'error']:
        count = by_status.get(status, 0)
        pct = (count / total * 100) if total > 0 else 0
        print(f"  {status}: {count} ({pct:.1f}%)")


def main():
    parser = argparse.ArgumentParser(
        description='Audit tool for tracking uploaded Wikipedia images'
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # add command
    add_parser = subparsers.add_parser('add', help='Add an image to track')
    add_parser.add_argument('article', help='Wikipedia article title')
    add_parser.add_argument('filename', help='Commons filename (with or without File: prefix)')
    add_parser.add_argument('--notes', help='Optional notes about the upload')

    # check command
    check_parser = subparsers.add_parser('check', help='Check if images are still present')
    check_group = check_parser.add_mutually_exclusive_group()
    check_group.add_argument('--all', action='store_true', help='Check all tracked images')
    check_group.add_argument('article', nargs='?', help='Check images for specific article')

    # list command
    list_parser = subparsers.add_parser('list', help='List tracked images')
    list_parser.add_argument('--status', choices=['present', 'removed', 'unknown', 'error'],
                            help='Filter by status')

    # stats command
    subparsers.add_parser('stats', help='Show statistics')

    args = parser.parse_args()

    # Initialize database
    init_db()

    if args.command == 'add':
        add_image(args.article, args.filename, args.notes)
    elif args.command == 'check':
        if args.all:
            check_all()
        elif args.article:
            check_article(args.article)
        else:
            check_all()
    elif args.command == 'list':
        list_images(args.status)
    elif args.command == 'stats':
        show_stats()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
