#!/usr/bin/env python3
"""
Migration: Add 'commons_status' column to careers table.

Tracks the diversity review status for a career's Wikimedia Commons category,
independently from the Wikipedia article review status.

Run:
    python migrations/migrate_add_commons_status.py
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

    if 'commons_status' in columns:
        print("Migration already applied - 'commons_status' column exists")
        conn.close()
        return

    print("Adding commons_status column...")
    conn.execute("ALTER TABLE careers ADD COLUMN commons_status TEXT DEFAULT 'unreviewed'")
    conn.commit()
    conn.close()
    print("Migration complete!")


if __name__ == '__main__':
    migrate()
