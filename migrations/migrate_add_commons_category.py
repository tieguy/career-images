#!/usr/bin/env python3
"""
Migration: Add 'commons_category' column to careers table.

This adds a text column to store the Wikimedia Commons category name
(from Wikidata P373 property) for each career.

Run:
    python migrations/migrate_add_commons_category.py
"""

import os
import sqlite3

DB_PATH = os.environ.get('DATABASE_PATH', 'careers.db')


def migrate():
    print(f"Migrating database: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)

    # Check if migration is needed
    cursor = conn.execute("PRAGMA table_info(careers)")
    columns = [row[1] for row in cursor.fetchall()]

    if 'commons_category' in columns:
        print("Migration already applied - 'commons_category' column exists")
        conn.close()
        return

    print("Adding commons_category column...")
    conn.execute("ALTER TABLE careers ADD COLUMN commons_category TEXT")
    conn.commit()
    conn.close()
    print("Migration complete!")


if __name__ == '__main__':
    migrate()
