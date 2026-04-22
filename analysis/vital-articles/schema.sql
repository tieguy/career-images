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
    seed          INTEGER,
    wikidata_id   TEXT  -- resolved lazily by fetch_qids.py; NULL until then
);

-- Cross-language sitelinks: which language Wikipedias have an article for each
-- sampled QID. Keyed on (qid, language) so one row per (article, wiki) pair.
-- Populated by fetch_sitelinks.py from a Wikidata SPARQL query. Scoped to
-- language Wikipedias only (not Commons, Wiktionary, etc.).
CREATE TABLE IF NOT EXISTS sitelinks (
    qid           TEXT NOT NULL,
    language      TEXT NOT NULL,
    foreign_title TEXT NOT NULL,
    PRIMARY KEY (qid, language)
);

-- Per-(QID, language) monthly pageviews for cross-language decline comparison.
-- Intentionally separate from monthly_views (which is en-wiki title-keyed):
-- cross-language analysis needs QID as the join key so translations line up.
-- English data stays in monthly_views; non-en lives here. Don't collapse —
-- the two tables have different provenance and recovery semantics.
CREATE TABLE IF NOT EXISTS cross_lang_monthly_views (
    qid      TEXT NOT NULL,
    language TEXT NOT NULL,
    year     INTEGER NOT NULL,
    month    INTEGER NOT NULL,
    views    INTEGER NOT NULL,
    PRIMARY KEY (qid, language, year, month)
);

-- Per-(QID, language) fetch status, parallel to pageview_fetch_log.
CREATE TABLE IF NOT EXISTS cross_lang_fetch_log (
    qid        TEXT NOT NULL,
    language   TEXT NOT NULL,
    fetched_at TEXT,
    status     TEXT NOT NULL CHECK(status IN ('ok', 'missing', 'error')),
    error      TEXT,
    PRIMARY KEY (qid, language)
);

-- Per-article edit/maintenance stats from XTools articleinfo.
-- revisions = total lifetime edits; editors = unique lifetime editors.
-- anon_edits, minor_edits are subsets of revisions. watchers is the
-- current watchlist subscriber count (community-interest proxy).
-- created_at is ISO8601; article_age is derived at query time.
CREATE TABLE IF NOT EXISTS article_stats (
    qid                TEXT NOT NULL,
    language           TEXT NOT NULL,
    revisions          INTEGER,
    editors            INTEGER,
    anon_edits         INTEGER,
    minor_edits        INTEGER,
    watchers           INTEGER,
    created_at         TEXT,
    fetched_at         TEXT NOT NULL,
    status             TEXT NOT NULL CHECK(status IN ('ok', 'missing', 'error')),
    error              TEXT,
    PRIMARY KEY (qid, language)
);

-- Freshness proxy for the "article staleness vs decline" hypothesis.
-- Per-(QID, language) current revision timestamp from each wiki's MW API.
-- Keyed on QID + language so the same table holds both en and non-en data.
-- rev_timestamp is ISO8601 in UTC (from the MW API, passed through verbatim).
CREATE TABLE IF NOT EXISTS article_freshness (
    qid            TEXT NOT NULL,
    language       TEXT NOT NULL,
    rev_id         INTEGER,
    rev_timestamp  TEXT,
    fetched_at     TEXT NOT NULL,
    status         TEXT NOT NULL CHECK(status IN ('ok', 'missing', 'error')),
    error          TEXT,
    PRIMARY KEY (qid, language)
);

-- Per-(QID, language) Lift Wing articlequality scores for non-en wikis.
-- Parallel to article_quality (title-keyed, en-only) but keyed on QID so
-- cross-language analyses line up the same Wikidata item. Populated by
-- fetch_quality_xlang.py. Only populated for languages whose own wiki has
-- a Lift Wing articlequality model (fr, pt, ru, uk, fa at time of writing).
CREATE TABLE IF NOT EXISTS article_quality_xlang (
    qid              TEXT NOT NULL,
    language         TEXT NOT NULL,
    rev_id           INTEGER,
    predicted_class  TEXT,
    expected_quality REAL,
    prob_stub        REAL,
    prob_start       REAL,
    prob_c           REAL,
    prob_b           REAL,
    prob_ga          REAL,
    prob_fa          REAL,
    fetched_at       TEXT NOT NULL,
    status           TEXT NOT NULL CHECK(status IN ('ok', 'missing_revid', 'model_error', 'http_error')),
    error            TEXT,
    PRIMARY KEY (qid, language)
);

-- Language-agnostic Lift Wing articlequality scalar (model_name='articlequality',
-- with both rev_id and lang as input). Returns a single 0-1 score per article,
-- comparable across wikis. Covers all 12 viable languages plus any other wiki
-- — unlike the per-{lang}wiki-articlequality classification models which only
-- exist for ~6 wikis. Used as the primary quality variable in the cross-language
-- multivariate regression.
CREATE TABLE IF NOT EXISTS article_quality_score (
    qid         TEXT NOT NULL,
    language    TEXT NOT NULL,
    rev_id      INTEGER,
    score       REAL,
    fetched_at  TEXT NOT NULL,
    status      TEXT NOT NULL CHECK(status IN ('ok', 'missing_revid', 'model_error', 'http_error')),
    error       TEXT,
    PRIMARY KEY (qid, language)
);

-- Per-article Lift Wing articlequality scores (successor to ORES).
-- prediction is the categorical argmax class (Stub/Start/C/B/GA/FA).
-- expected_quality is the probability-weighted score: Stub=0..FA=5; acts
-- as a continuous quality proxy suitable for regression against pct_change.
-- rev_id is the revision the model scored; stored so re-fetches are cheap.
CREATE TABLE IF NOT EXISTS article_quality (
    title             TEXT PRIMARY KEY,
    rev_id            INTEGER,
    predicted_class   TEXT,
    expected_quality  REAL,
    prob_stub         REAL,
    prob_start        REAL,
    prob_c            REAL,
    prob_b            REAL,
    prob_ga           REAL,
    prob_fa           REAL,
    fetched_at        TEXT NOT NULL,
    status            TEXT NOT NULL CHECK(status IN ('ok', 'missing_revid', 'model_error', 'http_error')),
    error             TEXT
);

CREATE INDEX IF NOT EXISTS idx_article_topics_topic ON article_topics(topic);
CREATE INDEX IF NOT EXISTS idx_monthly_views_year_month ON monthly_views(year, month);
CREATE INDEX IF NOT EXISTS idx_samples_topic ON samples(primary_topic);
CREATE INDEX IF NOT EXISTS idx_samples_wikidata ON samples(wikidata_id);
CREATE INDEX IF NOT EXISTS idx_sitelinks_language ON sitelinks(language);
CREATE INDEX IF NOT EXISTS idx_cross_lang_views_qid ON cross_lang_monthly_views(qid);
CREATE INDEX IF NOT EXISTS idx_cross_lang_views_ym ON cross_lang_monthly_views(language, year, month);
