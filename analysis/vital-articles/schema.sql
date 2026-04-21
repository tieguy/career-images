-- vital.db schema for the Vital Articles (Level 5) pageview analysis subproject.
--
-- Source of truth for the article→topic mapping is Wikipedia's on-wiki JSON
-- at Wikipedia:Vital_articles/data/{A..Z}.json, which is what the on-page
-- {{Vital article}} template reads.
--
-- Titles here use the on-wiki form (spaces, not underscores). The pageviews
-- API client in career-cliff/pageviews_api.py expects either form and encodes
-- appropriately, so we do not normalize at ingest time.

CREATE TABLE IF NOT EXISTS articles (
    title       TEXT PRIMARY KEY,
    level       INTEGER NOT NULL,
    source_file TEXT NOT NULL  -- e.g. 'A.json' (for provenance / debugging)
);

-- An article can be listed under multiple (topic, section) pairs in the on-wiki
-- data (e.g., a notable biologist appearing in both "Biology" and "People").
-- Stored as many-to-many so per-bucket aggregations can either dedupe or
-- multi-count as the analysis requires.
-- section defaults to '' (not NULL) so it can be part of the composite PK;
-- SQLite does not allow expressions like COALESCE(section, '') in PRIMARY KEY.
CREATE TABLE IF NOT EXISTS article_topics (
    title    TEXT NOT NULL,
    topic    TEXT NOT NULL,
    section  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (title, topic, section)
);

-- Tracks progress of the list-ingest step (one row per JSON data file).
CREATE TABLE IF NOT EXISTS ingest_log (
    source_file TEXT PRIMARY KEY,
    fetched_at  TEXT NOT NULL,
    status      TEXT NOT NULL CHECK(status IN ('ok', 'missing', 'error')),
    entry_count INTEGER,
    error       TEXT
);

-- Tracks per-article pageview fetch status, parallel to career-cliff/fetch_log.
-- Populated by the pageview fetcher (separate script, not by fetch_vital_list).
CREATE TABLE IF NOT EXISTS pageview_fetch_log (
    title      TEXT PRIMARY KEY,
    fetched_at TEXT,
    status     TEXT NOT NULL CHECK(status IN ('ok', 'missing', 'error')),
    error      TEXT
);

CREATE TABLE IF NOT EXISTS monthly_views (
    title TEXT    NOT NULL,
    year  INTEGER NOT NULL,
    month INTEGER NOT NULL,
    views INTEGER NOT NULL,
    PRIMARY KEY (title, year, month)
);

-- Stratified sample of articles to actually fetch pageviews for. Written by
-- sample.py and read by fetch_pageviews.py. Re-running sample.py replaces
-- the contents of this table. The primary_topic column is the single bucket
-- assigned for stratification purposes; the full multi-topic info remains in
-- article_topics.
CREATE TABLE IF NOT EXISTS samples (
    title         TEXT PRIMARY KEY,
    primary_topic TEXT NOT NULL,
    sampled_at    TEXT NOT NULL,
    seed          INTEGER
);

CREATE INDEX IF NOT EXISTS idx_article_topics_topic ON article_topics(topic);
CREATE INDEX IF NOT EXISTS idx_monthly_views_year_month ON monthly_views(year, month);
CREATE INDEX IF NOT EXISTS idx_samples_topic ON samples(primary_topic);
