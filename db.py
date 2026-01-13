"""
db.py - Database abstraction for career data storage

Supports SQLite for local development and MariaDB for Toolforge deployment.
Auto-detects environment based on presence of ~/replica.my.cnf
"""

import os
import sqlite3
from datetime import datetime
from typing import Optional
from contextlib import contextmanager

# Category mapping from Wikidata Q-IDs to readable names
# Only base classes need explicit mapping; subclasses default to 'profession'
BASE_CATEGORY_MAP = {
    'Q28640': 'profession',
    'Q12737077': 'occupation',
    'Q192581': 'job',
    'Q4164871': 'position',
    'Q136649946': 'position',
}


def get_category(qid: str) -> str:
    """Map a Wikidata Q-ID to a category name. Defaults to 'profession' for subclasses."""
    return BASE_CATEGORY_MAP.get(qid, 'profession')


# For backwards compatibility
CATEGORY_MAP = type('CategoryMap', (), {'get': lambda self, k, d=None: get_category(k)})()

# Valid status values for careers
VALID_STATUSES = ('unreviewed', 'needs_diverse_images', 'has_diverse_images', 'not_a_career', 'gender_specific')

# Pageview buckets for sorting (lower_bound, label)
# Sorted descending by traffic - careers sorted alphabetically within each bucket
PAGEVIEW_BUCKETS = [
    (2000, '>2,000'),
    (1000, '1,000–2,000'),
    (500, '500–1,000'),
    (200, '200–500'),
    (100, '100–200'),
    (50, '50–100'),
    (0, '<50'),
]


def get_pageview_bucket(avg_daily_views: float) -> tuple[int, str]:
    """
    Get bucket index and label for a pageview count.
    Returns (bucket_index, label) where lower index = higher traffic.
    """
    views = avg_daily_views or 0
    for i, (lower_bound, label) in enumerate(PAGEVIEW_BUCKETS):
        if views >= lower_bound:
            return (i, label)
    return (len(PAGEVIEW_BUCKETS) - 1, PAGEVIEW_BUCKETS[-1][1])


def is_toolforge() -> bool:
    """Check if running on Toolforge"""
    return os.path.exists(os.path.expanduser("~/replica.my.cnf"))


class Database:
    """Abstract base for database operations"""

    def init_schema(self):
        raise NotImplementedError

    def upsert_career(self, career: dict):
        raise NotImplementedError

    def upsert_careers(self, careers: list[dict]):
        raise NotImplementedError

    def get_careers_needing_pageviews(self) -> list[dict]:
        raise NotImplementedError

    def update_pageviews(self, wikidata_id: str, total_views: int, avg_daily: float):
        raise NotImplementedError

    def get_top_careers(self, limit: int = 20) -> list[dict]:
        raise NotImplementedError

    def get_career(self, wikidata_id: str) -> Optional[dict]:
        raise NotImplementedError

    def get_stats(self) -> dict:
        raise NotImplementedError

    def get_all_careers(self) -> list[dict]:
        raise NotImplementedError

    # Image methods
    def add_career_image(self, wikidata_id: str, image: dict):
        raise NotImplementedError

    def get_career_images(self, wikidata_id: str) -> list[dict]:
        raise NotImplementedError

    def clear_career_images(self, wikidata_id: str, source: str = None):
        raise NotImplementedError


class SQLiteDatabase(Database):
    """SQLite implementation for local development"""

    def __init__(self, db_path: str = "careers.db"):
        self.db_path = db_path

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def init_schema(self):
        """Create tables if they don't exist"""
        with self.get_connection() as conn:
            # Main careers table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS careers (
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

            # Career images table (one-to-many)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS career_images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wikidata_id TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    caption TEXT,
                    position INTEGER DEFAULT 0,
                    is_replacement INTEGER DEFAULT 0,
                    source TEXT DEFAULT 'wikipedia' CHECK(source IN ('wikipedia', 'openverse')),
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (wikidata_id) REFERENCES careers(wikidata_id)
                )
            """)

            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_avg_daily_views ON careers(avg_daily_views DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON careers(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_career_images_wikidata ON career_images(wikidata_id)")
            conn.commit()

    def upsert_career(self, career: dict):
        """Insert or update a single career"""
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO careers (wikidata_id, name, category, wikipedia_url, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(wikidata_id) DO UPDATE SET
                    name = excluded.name,
                    category = excluded.category,
                    wikipedia_url = excluded.wikipedia_url,
                    updated_at = excluded.updated_at
            """, (
                career['wikidata_id'],
                career['name'],
                career.get('category'),
                career.get('wikipedia_url'),
                datetime.now().isoformat()
            ))
            conn.commit()

    def upsert_careers(self, careers: list[dict]):
        """Batch insert or update careers"""
        with self.get_connection() as conn:
            now = datetime.now().isoformat()
            conn.executemany("""
                INSERT INTO careers (wikidata_id, name, category, wikipedia_url, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(wikidata_id) DO UPDATE SET
                    name = excluded.name,
                    category = excluded.category,
                    wikipedia_url = excluded.wikipedia_url,
                    updated_at = excluded.updated_at
            """, [
                (
                    c['wikidata_id'],
                    c['name'],
                    c.get('category'),
                    c.get('wikipedia_url'),
                    now
                )
                for c in careers
            ])
            conn.commit()

    def get_careers_needing_pageviews(self) -> list[dict]:
        """Get careers that don't have pageview data yet"""
        with self.get_connection() as conn:
            cursor = conn.execute("""
                SELECT wikidata_id, name, wikipedia_url
                FROM careers
                WHERE last_pageview_update IS NULL
                ORDER BY wikidata_id
            """)
            return [dict(row) for row in cursor.fetchall()]

    def update_pageviews(self, wikidata_id: str, total_views: int, avg_daily: float):
        """Update pageview data for a career"""
        with self.get_connection() as conn:
            now = datetime.now().isoformat()
            conn.execute("""
                UPDATE careers
                SET pageviews_total = ?,
                    avg_daily_views = ?,
                    last_pageview_update = ?,
                    updated_at = ?
                WHERE wikidata_id = ?
            """, (total_views, avg_daily, now, now, wikidata_id))
            conn.commit()

    def update_pageviews_batch(self, updates: list[tuple[str, int, float]]):
        """Batch update pageviews: list of (wikidata_id, total_views, avg_daily)"""
        with self.get_connection() as conn:
            now = datetime.now().isoformat()
            conn.executemany("""
                UPDATE careers
                SET pageviews_total = ?,
                    avg_daily_views = ?,
                    last_pageview_update = ?,
                    updated_at = ?
                WHERE wikidata_id = ?
            """, [(total, avg, now, now, wid) for wid, total, avg in updates])
            conn.commit()

    def get_top_careers(self, limit: int = 20) -> list[dict]:
        """Get top careers by pageviews"""
        with self.get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM careers
                WHERE pageviews_total > 0
                ORDER BY avg_daily_views DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_career(self, wikidata_id: str) -> Optional[dict]:
        """Get a single career by Wikidata ID"""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM careers WHERE wikidata_id = ?",
                (wikidata_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_careers_by_status(self, status: str, limit: int = 100) -> list[dict]:
        """Get careers filtered by review status, sorted by bucket then alphabetically"""
        with self.get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM careers
                WHERE status = ?
                ORDER BY avg_daily_views DESC
                LIMIT ?
            """, (status, limit))
            careers = [dict(row) for row in cursor.fetchall()]

        # Add bucket info and re-sort
        for career in careers:
            bucket_idx, bucket_label = get_pageview_bucket(career['avg_daily_views'] or 0)
            career['bucket_index'] = bucket_idx
            career['bucket_label'] = bucket_label

        careers.sort(key=lambda c: (c['bucket_index'], c['name'].lower()))
        return careers

    def update_career_status(self, wikidata_id: str, status: str,
                            reviewed_by: str = None, notes: str = None):
        """Update the review status of a career"""
        with self.get_connection() as conn:
            now = datetime.now().isoformat()
            conn.execute("""
                UPDATE careers
                SET status = ?,
                    reviewed_by = COALESCE(?, reviewed_by),
                    reviewed_at = ?,
                    notes = COALESCE(?, notes),
                    updated_at = ?
                WHERE wikidata_id = ?
            """, (status, reviewed_by, now, notes, now, wikidata_id))
            conn.commit()

    def update_career_lede(self, wikidata_id: str, lede_text: str):
        """Update the cached lede text for a career"""
        with self.get_connection() as conn:
            now = datetime.now().isoformat()
            conn.execute("""
                UPDATE careers
                SET lede_text = ?,
                    lede_fetched_at = ?,
                    updated_at = ?
                WHERE wikidata_id = ?
            """, (lede_text, now, now, wikidata_id))
            conn.commit()

    def get_stats(self) -> dict:
        """Get dataset statistics"""
        with self.get_connection() as conn:
            stats = {}

            # Total count
            cursor = conn.execute("SELECT COUNT(*) FROM careers")
            stats['total_careers'] = cursor.fetchone()[0]

            # With pageviews
            cursor = conn.execute("SELECT COUNT(*) FROM careers WHERE last_pageview_update IS NOT NULL")
            stats['with_pageviews'] = cursor.fetchone()[0]

            # Total views
            cursor = conn.execute("SELECT SUM(pageviews_total) FROM careers")
            stats['total_views'] = cursor.fetchone()[0] or 0

            # By category
            cursor = conn.execute("""
                SELECT category, COUNT(*) as count
                FROM careers
                GROUP BY category
            """)
            stats['by_category'] = {row[0]: row[1] for row in cursor.fetchall()}

            # By status
            cursor = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM careers
                GROUP BY status
            """)
            stats['by_status'] = {row[0]: row[1] for row in cursor.fetchall()}

            # Top career
            cursor = conn.execute("""
                SELECT name, pageviews_total
                FROM careers
                ORDER BY avg_daily_views DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                stats['top_career'] = {'name': row[0], 'views': row[1]}

            return stats

    def get_all_careers(self) -> list[dict]:
        """Get all careers, sorted by pageview bucket then alphabetically within bucket"""
        with self.get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM careers
                ORDER BY avg_daily_views DESC
            """)
            careers = [dict(row) for row in cursor.fetchall()]

        # Add bucket info and re-sort: by bucket index, then alphabetically
        for career in careers:
            bucket_idx, bucket_label = get_pageview_bucket(career['avg_daily_views'] or 0)
            career['bucket_index'] = bucket_idx
            career['bucket_label'] = bucket_label

        # Sort by bucket (high traffic first), then name alphabetically
        careers.sort(key=lambda c: (c['bucket_index'], c['name'].lower()))
        return careers

    def count(self) -> int:
        """Get total number of careers"""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM careers")
            return cursor.fetchone()[0]

    def search_careers(self, query: str, limit: int = 100) -> list[dict]:
        """Search careers by name, sorted by bucket then alphabetically"""
        # SECURITY: Escape SQL LIKE wildcards to prevent wildcard injection
        escaped_query = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        with self.get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM careers
                WHERE name LIKE ? ESCAPE '\\'
                ORDER BY avg_daily_views DESC
                LIMIT ?
            """, (f'%{escaped_query}%', limit))
            careers = [dict(row) for row in cursor.fetchall()]

        # Add bucket info and re-sort
        for career in careers:
            bucket_idx, bucket_label = get_pageview_bucket(career['avg_daily_views'] or 0)
            career['bucket_index'] = bucket_idx
            career['bucket_label'] = bucket_label

        careers.sort(key=lambda c: (c['bucket_index'], c['name'].lower()))
        return careers

    # Image methods

    def add_career_image(self, wikidata_id: str, image: dict):
        """Add an image to a career"""
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO career_images (wikidata_id, image_url, caption, position, is_replacement, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                wikidata_id,
                image['image_url'],
                image.get('caption'),
                image.get('position', 0),
                image.get('is_replacement', False),
                image.get('source', 'wikipedia')
            ))
            conn.commit()

    def add_career_images(self, wikidata_id: str, images: list[dict]):
        """Add multiple images to a career"""
        with self.get_connection() as conn:
            conn.executemany("""
                INSERT INTO career_images (wikidata_id, image_url, caption, position, is_replacement, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [
                (
                    wikidata_id,
                    img['image_url'],
                    img.get('caption'),
                    img.get('position', i),
                    img.get('is_replacement', False),
                    img.get('source', 'wikipedia')
                )
                for i, img in enumerate(images)
            ])
            # Update images_fetched_at timestamp
            conn.execute("""
                UPDATE careers SET images_fetched_at = ? WHERE wikidata_id = ?
            """, (datetime.now().isoformat(), wikidata_id))
            conn.commit()

    def get_career_images(self, wikidata_id: str, source: str = None) -> list[dict]:
        """Get all images for a career, optionally filtered by source"""
        with self.get_connection() as conn:
            if source:
                cursor = conn.execute("""
                    SELECT * FROM career_images
                    WHERE wikidata_id = ? AND source = ?
                    ORDER BY position
                """, (wikidata_id, source))
            else:
                cursor = conn.execute("""
                    SELECT * FROM career_images
                    WHERE wikidata_id = ?
                    ORDER BY position
                """, (wikidata_id,))
            return [dict(row) for row in cursor.fetchall()]

    def clear_career_images(self, wikidata_id: str, source: str = None):
        """Clear images for a career, optionally only from a specific source"""
        with self.get_connection() as conn:
            if source:
                conn.execute(
                    "DELETE FROM career_images WHERE wikidata_id = ? AND source = ?",
                    (wikidata_id, source)
                )
            else:
                conn.execute(
                    "DELETE FROM career_images WHERE wikidata_id = ?",
                    (wikidata_id,)
                )
            conn.commit()

    def set_replacement_image(self, wikidata_id: str, image_url: str, caption: str = None,
                              creator: str = None, license: str = None, license_url: str = None,
                              source_url: str = None):
        """Set an Openverse image as the selected replacement with metadata"""
        import json
        metadata = json.dumps({
            'creator': creator,
            'license': license,
            'license_url': license_url,
            'source_url': source_url,
        }) if any([creator, license, license_url, source_url]) else None

        with self.get_connection() as conn:
            # Ensure metadata column exists (for existing DBs)
            try:
                conn.execute("ALTER TABLE career_images ADD COLUMN metadata TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Clear any existing replacement
            conn.execute("""
                DELETE FROM career_images
                WHERE wikidata_id = ? AND is_replacement = 1
            """, (wikidata_id,))
            # Add the new replacement with metadata
            conn.execute("""
                INSERT INTO career_images (wikidata_id, image_url, caption, is_replacement, source, metadata)
                VALUES (?, ?, ?, 1, 'openverse', ?)
            """, (wikidata_id, image_url, caption, metadata))
            conn.commit()


class MariaDBDatabase(Database):
    """MariaDB implementation for Toolforge"""

    def __init__(self):
        """Initialize MariaDB connection using toolforge library or manual config"""
        try:
            # Try using toolforge library first (recommended approach)
            import toolforge
            self._use_toolforge_lib = True
            # Get tool name from environment or replica.my.cnf
            import configparser
            config = configparser.ConfigParser()
            config.read(os.path.expanduser("~/replica.my.cnf"))
            self.tool_user = config['client']['user']
            self.db_name = f"{self.tool_user}__careers"
        except ImportError:
            # Fall back to manual configuration
            self._use_toolforge_lib = False
            import configparser
            config = configparser.ConfigParser()
            config.read(os.path.expanduser("~/replica.my.cnf"))

            self.db_config = {
                'host': 'tools.db.svc.wikimedia.cloud',
                'user': config['client']['user'],
                'password': config['client']['password'],
            }
            self.db_name = f"{self.db_config['user']}__careers"

    @contextmanager
    def get_connection(self):
        """Get a database connection (context manager for proper cleanup)"""
        if self._use_toolforge_lib:
            import toolforge
            conn = toolforge.toolsdb(self.db_name)
        else:
            import pymysql
            conn = pymysql.connect(
                **self.db_config,
                database=self.db_name,
                cursorclass=pymysql.cursors.DictCursor
            )
        try:
            yield conn
        finally:
            conn.close()

    def init_schema(self):
        """Create database and tables if they don't exist"""
        import pymysql

        # First connect without database to create it
        if self._use_toolforge_lib:
            import toolforge
            # toolforge.toolsdb creates the database automatically
            conn = toolforge.toolsdb(self.db_name)
        else:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{self.db_name}`")
            cursor.close()
            conn.close()
            conn = pymysql.connect(**self.db_config, database=self.db_name)

        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS careers (
                wikidata_id VARCHAR(20) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                category ENUM('profession', 'occupation', 'job', 'position'),
                wikipedia_url VARCHAR(512),
                pageviews_total INT DEFAULT 0,
                avg_daily_views DECIMAL(10,2) DEFAULT 0,
                last_pageview_update DATETIME,
                status ENUM('unreviewed', 'needs_diverse_images', 'has_diverse_images', 'not_a_career', 'gender_specific') DEFAULT 'unreviewed',
                reviewed_by VARCHAR(255),
                reviewed_at DATETIME,
                notes TEXT,
                lede_text TEXT,
                lede_fetched_at DATETIME,
                images_fetched_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME ON UPDATE CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS career_images (
                id INT AUTO_INCREMENT PRIMARY KEY,
                wikidata_id VARCHAR(20) NOT NULL,
                image_url VARCHAR(512) NOT NULL,
                caption TEXT,
                position INT DEFAULT 0,
                is_replacement BOOLEAN DEFAULT FALSE,
                source ENUM('wikipedia', 'openverse') DEFAULT 'wikipedia',
                metadata TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (wikidata_id) REFERENCES careers(wikidata_id)
            )
        """)

        # Create indexes (ignore errors if they already exist)
        try:
            cursor.execute("CREATE INDEX idx_avg_daily_views ON careers(avg_daily_views DESC)")
        except pymysql.err.OperationalError:
            pass  # Index already exists
        try:
            cursor.execute("CREATE INDEX idx_status ON careers(status)")
        except pymysql.err.OperationalError:
            pass
        try:
            cursor.execute("CREATE INDEX idx_career_images_wikidata ON career_images(wikidata_id)")
        except pymysql.err.OperationalError:
            pass

        conn.commit()
        cursor.close()
        conn.close()

    def _row_to_dict(self, cursor, row) -> dict:
        """Convert a database row to a dictionary"""
        if row is None:
            return None
        if isinstance(row, dict):
            return row
        columns = [col[0] for col in cursor.description]
        return dict(zip(columns, row))

    def upsert_career(self, career: dict):
        """Insert or update a single career"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("""
                INSERT INTO careers (wikidata_id, name, category, wikipedia_url, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    category = VALUES(category),
                    wikipedia_url = VALUES(wikipedia_url),
                    updated_at = VALUES(updated_at)
            """, (
                career['wikidata_id'],
                career['name'],
                career.get('category'),
                career.get('wikipedia_url'),
                now
            ))
            conn.commit()
            cursor.close()

    def upsert_careers(self, careers: list[dict]):
        """Batch insert or update careers"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.executemany("""
                INSERT INTO careers (wikidata_id, name, category, wikipedia_url, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    category = VALUES(category),
                    wikipedia_url = VALUES(wikipedia_url),
                    updated_at = VALUES(updated_at)
            """, [
                (
                    c['wikidata_id'],
                    c['name'],
                    c.get('category'),
                    c.get('wikipedia_url'),
                    now
                )
                for c in careers
            ])
            conn.commit()
            cursor.close()

    def get_careers_needing_pageviews(self) -> list[dict]:
        """Get careers that don't have pageview data yet"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT wikidata_id, name, wikipedia_url
                FROM careers
                WHERE last_pageview_update IS NULL
                ORDER BY wikidata_id
            """)
            rows = cursor.fetchall()
            result = [self._row_to_dict(cursor, row) for row in rows]
            cursor.close()
            return result

    def update_pageviews(self, wikidata_id: str, total_views: int, avg_daily: float):
        """Update pageview data for a career"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("""
                UPDATE careers
                SET pageviews_total = %s,
                    avg_daily_views = %s,
                    last_pageview_update = %s,
                    updated_at = %s
                WHERE wikidata_id = %s
            """, (total_views, avg_daily, now, now, wikidata_id))
            conn.commit()
            cursor.close()

    def update_pageviews_batch(self, updates: list[tuple[str, int, float]]):
        """Batch update pageviews: list of (wikidata_id, total_views, avg_daily)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.executemany("""
                UPDATE careers
                SET pageviews_total = %s,
                    avg_daily_views = %s,
                    last_pageview_update = %s,
                    updated_at = %s
                WHERE wikidata_id = %s
            """, [(total, avg, now, now, wid) for wid, total, avg in updates])
            conn.commit()
            cursor.close()

    def get_top_careers(self, limit: int = 20) -> list[dict]:
        """Get top careers by pageviews"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM careers
                WHERE pageviews_total > 0
                ORDER BY avg_daily_views DESC
                LIMIT %s
            """, (limit,))
            rows = cursor.fetchall()
            result = [self._row_to_dict(cursor, row) for row in rows]
            cursor.close()
            return result

    def get_career(self, wikidata_id: str) -> Optional[dict]:
        """Get a single career by Wikidata ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM careers WHERE wikidata_id = %s",
                (wikidata_id,)
            )
            row = cursor.fetchone()
            result = self._row_to_dict(cursor, row)
            cursor.close()
            return result

    def get_careers_by_status(self, status: str, limit: int = 100) -> list[dict]:
        """Get careers filtered by review status, sorted by bucket then alphabetically"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM careers
                WHERE status = %s
                ORDER BY avg_daily_views DESC
                LIMIT %s
            """, (status, limit))
            rows = cursor.fetchall()
            careers = [self._row_to_dict(cursor, row) for row in rows]
            cursor.close()

        # Add bucket info and re-sort
        for career in careers:
            avg_views = career.get('avg_daily_views') or 0
            if hasattr(avg_views, '__float__'):
                avg_views = float(avg_views)
            bucket_idx, bucket_label = get_pageview_bucket(avg_views)
            career['bucket_index'] = bucket_idx
            career['bucket_label'] = bucket_label

        careers.sort(key=lambda c: (c['bucket_index'], c['name'].lower()))
        return careers

    def update_career_status(self, wikidata_id: str, status: str,
                            reviewed_by: str = None, notes: str = None):
        """Update the review status of a career"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if reviewed_by and notes:
                cursor.execute("""
                    UPDATE careers
                    SET status = %s, reviewed_by = %s, reviewed_at = %s, notes = %s, updated_at = %s
                    WHERE wikidata_id = %s
                """, (status, reviewed_by, now, notes, now, wikidata_id))
            elif reviewed_by:
                cursor.execute("""
                    UPDATE careers
                    SET status = %s, reviewed_by = %s, reviewed_at = %s, updated_at = %s
                    WHERE wikidata_id = %s
                """, (status, reviewed_by, now, now, wikidata_id))
            elif notes:
                cursor.execute("""
                    UPDATE careers
                    SET status = %s, reviewed_at = %s, notes = %s, updated_at = %s
                    WHERE wikidata_id = %s
                """, (status, now, notes, now, wikidata_id))
            else:
                cursor.execute("""
                    UPDATE careers
                    SET status = %s, reviewed_at = %s, updated_at = %s
                    WHERE wikidata_id = %s
                """, (status, now, now, wikidata_id))
            conn.commit()
            cursor.close()

    def update_career_lede(self, wikidata_id: str, lede_text: str):
        """Update the cached lede text for a career"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("""
                UPDATE careers
                SET lede_text = %s, lede_fetched_at = %s, updated_at = %s
                WHERE wikidata_id = %s
            """, (lede_text, now, now, wikidata_id))
            conn.commit()
            cursor.close()

    def get_stats(self) -> dict:
        """Get dataset statistics"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            stats = {}

            # Total count
            cursor.execute("SELECT COUNT(*) FROM careers")
            stats['total_careers'] = cursor.fetchone()[0]

            # With pageviews
            cursor.execute("SELECT COUNT(*) FROM careers WHERE last_pageview_update IS NOT NULL")
            stats['with_pageviews'] = cursor.fetchone()[0]

            # Total views
            cursor.execute("SELECT SUM(pageviews_total) FROM careers")
            result = cursor.fetchone()[0]
            stats['total_views'] = int(result) if result else 0

            # By category
            cursor.execute("""
                SELECT category, COUNT(*) as count
                FROM careers
                GROUP BY category
            """)
            stats['by_category'] = {row[0]: row[1] for row in cursor.fetchall()}

            # By status
            cursor.execute("""
                SELECT status, COUNT(*) as count
                FROM careers
                GROUP BY status
            """)
            stats['by_status'] = {row[0]: row[1] for row in cursor.fetchall()}

            # Top career
            cursor.execute("""
                SELECT name, pageviews_total
                FROM careers
                ORDER BY avg_daily_views DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                stats['top_career'] = {'name': row[0], 'views': row[1]}

            cursor.close()
            return stats

    def get_all_careers(self) -> list[dict]:
        """Get all careers, sorted by pageview bucket then alphabetically within bucket"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM careers
                ORDER BY avg_daily_views DESC
            """)
            rows = cursor.fetchall()
            careers = [self._row_to_dict(cursor, row) for row in rows]
            cursor.close()

        # Add bucket info and re-sort
        for career in careers:
            avg_views = career.get('avg_daily_views') or 0
            if hasattr(avg_views, '__float__'):
                avg_views = float(avg_views)
            bucket_idx, bucket_label = get_pageview_bucket(avg_views)
            career['bucket_index'] = bucket_idx
            career['bucket_label'] = bucket_label

        # Sort by bucket (high traffic first), then name alphabetically
        careers.sort(key=lambda c: (c['bucket_index'], c['name'].lower()))
        return careers

    def count(self) -> int:
        """Get total number of careers"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM careers")
            result = cursor.fetchone()[0]
            cursor.close()
            return result

    def search_careers(self, query: str, limit: int = 100) -> list[dict]:
        """Search careers by name, sorted by bucket then alphabetically"""
        # SECURITY: Escape SQL LIKE wildcards to prevent wildcard injection
        escaped_query = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM careers
                WHERE name LIKE %s ESCAPE '\\\\'
                ORDER BY avg_daily_views DESC
                LIMIT %s
            """, (f'%{escaped_query}%', limit))
            rows = cursor.fetchall()
            careers = [self._row_to_dict(cursor, row) for row in rows]
            cursor.close()

        # Add bucket info and re-sort
        for career in careers:
            avg_views = career.get('avg_daily_views') or 0
            if hasattr(avg_views, '__float__'):
                avg_views = float(avg_views)
            bucket_idx, bucket_label = get_pageview_bucket(avg_views)
            career['bucket_index'] = bucket_idx
            career['bucket_label'] = bucket_label

        careers.sort(key=lambda c: (c['bucket_index'], c['name'].lower()))
        return careers

    # Image methods

    def add_career_image(self, wikidata_id: str, image: dict):
        """Add an image to a career"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO career_images (wikidata_id, image_url, caption, position, is_replacement, source)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                wikidata_id,
                image['image_url'],
                image.get('caption'),
                image.get('position', 0),
                image.get('is_replacement', False),
                image.get('source', 'wikipedia')
            ))
            conn.commit()
            cursor.close()

    def add_career_images(self, wikidata_id: str, images: list[dict]):
        """Add multiple images to a career"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany("""
                INSERT INTO career_images (wikidata_id, image_url, caption, position, is_replacement, source)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, [
                (
                    wikidata_id,
                    img['image_url'],
                    img.get('caption'),
                    img.get('position', i),
                    img.get('is_replacement', False),
                    img.get('source', 'wikipedia')
                )
                for i, img in enumerate(images)
            ])
            # Update images_fetched_at timestamp
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("""
                UPDATE careers SET images_fetched_at = %s WHERE wikidata_id = %s
            """, (now, wikidata_id))
            conn.commit()
            cursor.close()

    def get_career_images(self, wikidata_id: str, source: str = None) -> list[dict]:
        """Get all images for a career, optionally filtered by source"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if source:
                cursor.execute("""
                    SELECT * FROM career_images
                    WHERE wikidata_id = %s AND source = %s
                    ORDER BY position
                """, (wikidata_id, source))
            else:
                cursor.execute("""
                    SELECT * FROM career_images
                    WHERE wikidata_id = %s
                    ORDER BY position
                """, (wikidata_id,))
            rows = cursor.fetchall()
            result = [self._row_to_dict(cursor, row) for row in rows]
            cursor.close()
            return result

    def clear_career_images(self, wikidata_id: str, source: str = None):
        """Clear images for a career, optionally only from a specific source"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if source:
                cursor.execute(
                    "DELETE FROM career_images WHERE wikidata_id = %s AND source = %s",
                    (wikidata_id, source)
                )
            else:
                cursor.execute(
                    "DELETE FROM career_images WHERE wikidata_id = %s",
                    (wikidata_id,)
                )
            conn.commit()
            cursor.close()

    def set_replacement_image(self, wikidata_id: str, image_url: str, caption: str = None,
                              creator: str = None, license: str = None, license_url: str = None,
                              source_url: str = None):
        """Set an Openverse image as the selected replacement with metadata"""
        import json
        metadata = json.dumps({
            'creator': creator,
            'license': license,
            'license_url': license_url,
            'source_url': source_url,
        }) if any([creator, license, license_url, source_url]) else None

        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Clear any existing replacement
            cursor.execute("""
                DELETE FROM career_images
                WHERE wikidata_id = %s AND is_replacement = 1
            """, (wikidata_id,))
            # Add the new replacement with metadata
            cursor.execute("""
                INSERT INTO career_images (wikidata_id, image_url, caption, is_replacement, source, metadata)
                VALUES (%s, %s, %s, 1, 'openverse', %s)
            """, (wikidata_id, image_url, caption, metadata))
            conn.commit()
            cursor.close()


def get_database() -> Database:
    """Get appropriate database instance based on environment"""
    if is_toolforge():
        return MariaDBDatabase()
    else:
        db_path = os.environ.get('DATABASE_PATH', 'careers.db')
        return SQLiteDatabase(db_path)
