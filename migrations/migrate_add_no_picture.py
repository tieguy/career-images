#!/usr/bin/env python3
"""
Migration: Add 'no_picture' status to careers table.

Run on fly.dev:
    python migrate_add_no_picture.py

The SQLite CHECK constraint needs the table recreated to add new enum values.
"""

import os
import sqlite3

DB_PATH = os.environ.get('DATABASE_PATH', 'careers.db')

def migrate():
    print(f"Migrating database: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)

    # Check if migration is needed
    cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='careers'")
    schema = cursor.fetchone()[0]

    if 'no_picture' in schema:
        print("Migration already applied - 'no_picture' status exists")
        conn.close()
        return

    print("Applying migration...")

    conn.executescript('''
        CREATE TABLE careers_new (
            wikidata_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT CHECK(category IN ('profession', 'occupation', 'job', 'position')),
            wikipedia_url TEXT,
            pageviews_total INTEGER DEFAULT 0,
            avg_daily_views REAL DEFAULT 0,
            last_pageview_update TEXT,
            status TEXT DEFAULT 'unreviewed'
                CHECK(status IN ('unreviewed', 'no_picture', 'needs_diverse_images', 'has_diverse_images', 'not_a_career', 'gender_specific')),
            reviewed_by TEXT,
            reviewed_at TEXT,
            notes TEXT,
            lede_text TEXT,
            lede_fetched_at TEXT,
            images_fetched_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        INSERT INTO careers_new SELECT * FROM careers;

        DROP TABLE careers;

        ALTER TABLE careers_new RENAME TO careers;

        CREATE INDEX idx_avg_daily_views ON careers(avg_daily_views DESC);
        CREATE INDEX idx_status ON careers(status);
    ''')

    conn.close()
    print("Migration complete!")

if __name__ == '__main__':
    migrate()
