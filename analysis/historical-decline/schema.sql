-- history.db schema for the historical pageview decline analysis subproject.
-- See docs/design-plans/2026-04-18-historical-pageview-analysis.md

CREATE TABLE IF NOT EXISTS annual_totals (
    wikidata_id TEXT NOT NULL,
    title       TEXT NOT NULL,
    year        INTEGER NOT NULL,
    views       INTEGER NOT NULL,
    rank        INTEGER,
    PRIMARY KEY (wikidata_id, year)
);

CREATE TABLE IF NOT EXISTS monthly_views (
    wikidata_id TEXT NOT NULL,
    title       TEXT NOT NULL,
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    views       INTEGER NOT NULL,
    PRIMARY KEY (wikidata_id, year, month)
);

CREATE TABLE IF NOT EXISTS ever_top (
    wikidata_id    TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    first_top_year INTEGER NOT NULL,
    last_top_year  INTEGER NOT NULL,
    years_in_top   INTEGER NOT NULL,
    peak_rank      INTEGER NOT NULL,
    peak_year      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_log (
    wikidata_id TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    fetched_at  TEXT,
    status      TEXT NOT NULL CHECK(status IN ('ok', 'missing', 'error')),
    error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_annual_totals_year_rank ON annual_totals(year, rank);
CREATE INDEX IF NOT EXISTS idx_fetch_log_status ON fetch_log(status);
CREATE INDEX IF NOT EXISTS idx_monthly_views_year_month ON monthly_views(year, month);
