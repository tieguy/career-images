#!/usr/bin/env python3
"""
Migration script to update status values in the database.

Changes:
- needs_image → needs_diverse_images
- has_image → has_diverse_images
- not_applicable → not_a_career (default, can be manually changed to gender_specific)

SQLite doesn't support modifying CHECK constraints, so we:
1. Create a new table with the new constraint
2. Copy data with status mapping
3. Drop old table
4. Rename new table
"""

import sqlite3
import sys
from datetime import datetime

def migrate(db_path: str = "careers.db"):
    print(f"Migrating database: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Check current schema
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='careers'")
    row = cursor.fetchone()
    if not row:
        print("No careers table found, nothing to migrate")
        return

    schema = row[0]

    # Check if already migrated
    if 'needs_diverse_images' in schema:
        print("Already migrated to new status values")
        return

    print("Current schema uses old status values, migrating...")

    # Count records by status
    cursor.execute("SELECT status, COUNT(*) FROM careers GROUP BY status")
    for status, count in cursor.fetchall():
        print(f"  {status}: {count}")

    # Create new table with updated schema
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS careers_new (
            wikidata_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT CHECK(category IN ('profession', 'occupation', 'job', 'position')),
            wikipedia_url TEXT,
            pageviews_total INTEGER DEFAULT 0,
            avg_daily_views REAL DEFAULT 0,
            last_pageview_update TEXT,

            -- Review state
            status TEXT DEFAULT 'unreviewed'
                CHECK(status IN ('unreviewed', 'needs_diverse_images', 'has_diverse_images', 'not_a_career', 'gender_specific')),
            reviewed_by TEXT,
            reviewed_at TEXT,
            notes TEXT,

            -- Cached content
            lede_text TEXT,
            lede_fetched_at TEXT,
            images_fetched_at TEXT,

            -- Timestamps
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Copy data with status mapping
    cursor.execute("""
        INSERT INTO careers_new
        SELECT
            wikidata_id,
            name,
            category,
            wikipedia_url,
            pageviews_total,
            avg_daily_views,
            last_pageview_update,
            CASE status
                WHEN 'needs_image' THEN 'needs_diverse_images'
                WHEN 'has_image' THEN 'has_diverse_images'
                WHEN 'not_applicable' THEN 'not_a_career'
                ELSE status
            END,
            reviewed_by,
            reviewed_at,
            notes,
            lede_text,
            lede_fetched_at,
            images_fetched_at,
            created_at,
            updated_at
        FROM careers
    """)

    # Drop old table and rename
    cursor.execute("DROP TABLE careers")
    cursor.execute("ALTER TABLE careers_new RENAME TO careers")

    # Recreate indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_avg_daily_views ON careers(avg_daily_views DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON careers(status)")

    conn.commit()

    # Verify migration
    cursor.execute("SELECT status, COUNT(*) FROM careers GROUP BY status")
    print("\nAfter migration:")
    for status, count in cursor.fetchall():
        print(f"  {status}: {count}")

    print("\nMigration complete!")
    conn.close()

if __name__ == '__main__':
    db_path = sys.argv[1] if len(sys.argv) > 1 else "careers.db"
    migrate(db_path)
