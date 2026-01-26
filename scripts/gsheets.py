#!/usr/bin/env python3
"""
gsheets.py - Google Sheets integration for career image tracking.

This module syncs career data to a Google Sheet for collaborative review.
Reviewers can update the sheet directly, and changes sync back to the local DB.

Setup:
1. Create a Google Cloud project and enable the Sheets API
2. Create a service account and download the JSON credentials
3. Save credentials to ~/.config/gspread/service_account.json
4. Share your target spreadsheet with the service account email

Usage:
    uv run python gsheets.py setup <spreadsheet_url>  # Initial setup
    uv run python gsheets.py push                     # Push local DB to sheet
    uv run python gsheets.py pull                     # Pull sheet updates to local DB
    uv run python gsheets.py sync                     # Bidirectional sync
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

try:
    import gspread
    from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

from db import get_database, VALID_STATUSES

CONFIG_FILE = Path("gsheets_config.json")

# Column headers for the sheet
HEADERS = [
    'Wikidata ID',
    'Career Name',
    'Category',
    'Wikipedia URL',
    'Avg Daily Views',
    'View Bucket',
    'Status',
    'Reviewed By',
    'Reviewed At',
    'Notes',
    'Last Synced',
]


def load_config() -> dict:
    """Load Google Sheets configuration."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(config: dict):
    """Save Google Sheets configuration."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def get_client():
    """Get authenticated gspread client."""
    if not GSPREAD_AVAILABLE:
        raise ImportError("gspread not installed. Run: uv sync")

    # Try service account first
    try:
        return gspread.service_account()
    except FileNotFoundError:
        pass

    # Try OAuth
    try:
        return gspread.oauth()
    except Exception as e:
        raise RuntimeError(
            "Could not authenticate with Google Sheets.\n"
            "Either:\n"
            "1. Place service account JSON at ~/.config/gspread/service_account.json\n"
            "2. Or run gspread OAuth flow\n"
            f"Error: {e}"
        )


def setup_sheet(spreadsheet_url: str):
    """Set up a spreadsheet for syncing."""
    client = get_client()

    try:
        spreadsheet = client.open_by_url(spreadsheet_url)
    except SpreadsheetNotFound:
        print(f"Error: Could not access spreadsheet at {spreadsheet_url}")
        print("Make sure you've shared it with your service account email.")
        return

    # Check for or create 'Careers' worksheet
    try:
        worksheet = spreadsheet.worksheet('Careers')
        print(f"Found existing 'Careers' worksheet")
    except WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet('Careers', rows=5000, cols=len(HEADERS))
        print(f"Created new 'Careers' worksheet")

    # Set headers if not present
    current_headers = worksheet.row_values(1)
    if current_headers != HEADERS:
        worksheet.update('A1', [HEADERS])
        print("Set column headers")

    # Save config
    config = {
        'spreadsheet_url': spreadsheet_url,
        'spreadsheet_id': spreadsheet.id,
        'worksheet_name': 'Careers',
        'last_sync': None,
    }
    save_config(config)

    print(f"\nSetup complete!")
    print(f"Spreadsheet: {spreadsheet.title}")
    print(f"Worksheet: Careers")
    print(f"\nRun 'python gsheets.py push' to populate the sheet.")


def push_to_sheet():
    """Push local database data to Google Sheet."""
    config = load_config()
    if not config.get('spreadsheet_url'):
        print("Error: No spreadsheet configured. Run 'python gsheets.py setup <url>' first.")
        return

    client = get_client()
    spreadsheet = client.open_by_url(config['spreadsheet_url'])
    worksheet = spreadsheet.worksheet(config['worksheet_name'])

    db = get_database()
    careers = db.get_all_careers()

    print(f"Pushing {len(careers)} careers to Google Sheet...")

    # Prepare data rows
    now = datetime.now().isoformat()
    rows = [[
        c['wikidata_id'],
        c['name'],
        c.get('category', ''),
        c.get('wikipedia_url', ''),
        c.get('avg_daily_views', 0),
        c.get('bucket_label', ''),
        c.get('status', 'unreviewed'),
        c.get('reviewed_by', ''),
        c.get('reviewed_at', ''),
        c.get('notes', ''),
        now,
    ] for c in careers]

    # Clear existing data (except headers) and add new
    worksheet.clear()
    worksheet.update('A1', [HEADERS] + rows)

    # Update config
    config['last_sync'] = now
    config['last_push'] = now
    save_config(config)

    print(f"Pushed {len(rows)} rows to sheet.")
    print(f"View at: {config['spreadsheet_url']}")


def pull_from_sheet():
    """Pull updates from Google Sheet to local database."""
    config = load_config()
    if not config.get('spreadsheet_url'):
        print("Error: No spreadsheet configured. Run 'python gsheets.py setup <url>' first.")
        return

    client = get_client()
    spreadsheet = client.open_by_url(config['spreadsheet_url'])
    worksheet = spreadsheet.worksheet(config['worksheet_name'])

    db = get_database()

    # Get all data from sheet
    records = worksheet.get_all_records()
    print(f"Pulling {len(records)} records from Google Sheet...")

    updates = 0
    for record in records:
        wikidata_id = record.get('Wikidata ID', '')
        if not wikidata_id:
            continue

        # Get current career from DB
        career = db.get_career(wikidata_id)
        if not career:
            continue

        # Check for status changes
        sheet_status = record.get('Status', '').strip()
        if sheet_status and sheet_status in VALID_STATUSES:
            if sheet_status != career['status']:
                db.update_career_status(
                    wikidata_id,
                    sheet_status,
                    reviewed_by=record.get('Reviewed By', 'gsheets'),
                    notes=record.get('Notes', '')
                )
                updates += 1
                print(f"  Updated {career['name']}: {career['status']} → {sheet_status}")

    config['last_sync'] = datetime.now().isoformat()
    config['last_pull'] = config['last_sync']
    save_config(config)

    print(f"\nPulled {updates} updates from sheet.")


def sync_bidirectional():
    """Bidirectional sync: pull updates then push current state."""
    print("=== Pulling updates from sheet ===")
    pull_from_sheet()
    print("\n=== Pushing current state to sheet ===")
    push_to_sheet()


def show_status():
    """Show sync status."""
    config = load_config()

    if not config:
        print("No Google Sheets sync configured.")
        print("Run 'python gsheets.py setup <spreadsheet_url>' to set up.")
        return

    print("Google Sheets Sync Status")
    print("=" * 40)
    print(f"Spreadsheet URL: {config.get('spreadsheet_url', 'Not set')}")
    print(f"Worksheet: {config.get('worksheet_name', 'Not set')}")
    print(f"Last sync: {config.get('last_sync', 'Never')}")
    print(f"Last push: {config.get('last_push', 'Never')}")
    print(f"Last pull: {config.get('last_pull', 'Never')}")


def main():
    parser = argparse.ArgumentParser(
        description='Google Sheets integration for career image tracking'
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # setup command
    setup_parser = subparsers.add_parser('setup', help='Set up a spreadsheet for syncing')
    setup_parser.add_argument('url', help='Google Spreadsheet URL')

    # push command
    subparsers.add_parser('push', help='Push local DB to Google Sheet')

    # pull command
    subparsers.add_parser('pull', help='Pull updates from Google Sheet to local DB')

    # sync command
    subparsers.add_parser('sync', help='Bidirectional sync')

    # status command
    subparsers.add_parser('status', help='Show sync status')

    args = parser.parse_args()

    if not GSPREAD_AVAILABLE:
        print("Error: gspread not installed.")
        print("Run: uv sync")
        return

    if args.command == 'setup':
        setup_sheet(args.url)
    elif args.command == 'push':
        push_to_sheet()
    elif args.command == 'pull':
        pull_from_sheet()
    elif args.command == 'sync':
        sync_bidirectional()
    elif args.command == 'status':
        show_status()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
