# Historical Pageview Analysis — Phase 1: Scaffolding and Schema

> **For Claude:** REQUIRED SUB-SKILL: Use ed3d-plan-and-execute:executing-an-implementation-plan to implement this plan task-by-task.

**Goal:** Create the `analysis/historical-decline/` subdirectory, a SQLite schema for `history.db`, a thin DB helper module, and a one-shot initializer script. No functional data fetching yet.

**Architecture:** Standalone subdirectory inside the repo, SQLite-only, isolated from the live app's database abstraction. Uses the existing `uv` environment — no new runtime dependencies.

**Tech Stack:** Python 3.13, sqlite3 stdlib, existing `pyproject.toml`.

**Scope:** Phase 1 of 4 in the historical pageview analysis design (`docs/design-plans/2026-04-18-historical-pageview-analysis.md`).

**Codebase verified:** 2026-04-18. Confirmed `analysis/` directory does not exist; `careers` table primary key is `wikidata_id` (not `wikidata_qid`); test framework is pytest with `responses==0.25.3`; `conftest.py` exists in `tests/` with `temp_db`, `populated_db`, `mock_openverse_response` fixtures.

**Deviation from design:** The design used `wikidata_qid` as the column name in `history.db`. Aligned to the existing `careers.db` convention: the new column is `wikidata_id` throughout. All subsequent phases follow this naming.

---

## Task 1: Create subdirectory and README

**Files:**
- Create: `analysis/historical-decline/README.md`

**Step 1: Create the directory**

```bash
mkdir -p analysis/historical-decline
```

Note: the hyphen in `historical-decline` means this directory is not importable as a Python package — the scripts in it will be run as CLIs via `uv run python analysis/historical-decline/<script>.py`, matching the project's existing script style in `scripts/`. For the same reason, we intentionally do NOT add an `__init__.py` here: it would be dead weight since the path cannot be imported anyway. Scripts manipulate `sys.path` directly (see Phase 2) to import sibling modules.

**Step 2: Create `analysis/historical-decline/README.md`**

```markdown
# Historical Pageview Decline Analysis

Companion analysis to Monperrus's [wikipedia-decline-llm](https://github.com/monperrus/wikipedia-decline-llm) paper, narrowed to the ~4,000 career articles in `careers.db`.

Asks: have Wikipedia pageviews for career articles declined over 2016–2025, and which articles fell the furthest?

## Pipeline

Run in order:

```bash
uv run python analysis/historical-decline/init_db.py
uv run python analysis/historical-decline/fetch_history.py fetch
uv run python analysis/historical-decline/compute_rankings.py
uv run python analysis/historical-decline/report.py
```

## Data

All outputs go to `analysis/historical-decline/history.db` (SQLite). The live app's `careers.db` is read-only from this subproject's perspective.

## Design

See `docs/design-plans/2026-04-18-historical-pageview-analysis.md`.
```

**Step 3: Verify**

```bash
ls analysis/historical-decline/
```
Expected: `README.md`

**Step 4: Commit**

```bash
git add analysis/historical-decline/README.md
git commit -m "chore: scaffold analysis/historical-decline subproject"
```

---

## Task 2: Create schema.sql

**Files:**
- Create: `analysis/historical-decline/schema.sql`

**Step 1: Write the schema**

```sql
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
```

**Step 2: Commit**

```bash
git add analysis/historical-decline/schema.sql
git commit -m "feat(analysis): add history.db schema"
```

---

## Task 3: Create history_db.py

**Files:**
- Create: `analysis/historical-decline/history_db.py`

**Step 1: Write the module**

```python
"""SQLite wrapper for the historical pageview decline subproject.

Kept deliberately minimal and separate from the live app's db.py (which juggles
SQLite vs MariaDB for Toolforge). This subproject is SQLite-only.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path(__file__).parent / "history.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection with Row factory. Caller is responsible for closing."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """Context-managed connection, mirroring db.py's pattern."""
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_schema(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Apply schema.sql to the given database. Idempotent."""
    schema = SCHEMA_PATH.read_text()
    with get_connection(db_path) as conn:
        conn.executescript(schema)
        conn.commit()


def table_names(db_path: Path | str = DEFAULT_DB_PATH) -> list[str]:
    """Return the list of user table names in the database."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return [r["name"] for r in rows]
```

**Step 2: Commit**

```bash
git add analysis/historical-decline/history_db.py
git commit -m "feat(analysis): add history_db connection helpers"
```

---

## Task 4: Create init_db.py CLI

**Files:**
- Create: `analysis/historical-decline/init_db.py`

**Step 1: Write the CLI**

```python
"""One-shot initializer for history.db.

Usage:
    uv run python analysis/historical-decline/init_db.py [--db PATH]

Idempotent: running twice is a no-op because schema uses CREATE TABLE IF NOT EXISTS.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from history_db import DEFAULT_DB_PATH, init_schema, table_names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Path to history.db (default: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()

    init_schema(args.db)
    tables = table_names(args.db)
    print(f"Initialized {args.db}")
    print(f"Tables: {', '.join(tables)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note on imports: because `analysis/historical-decline/` is not a Python package (the hyphen prevents that), the script imports `history_db` as a top-level module. The script must be run with the current working directory set so Python can find it. The simplest convention, matching existing scripts like `scripts/audit.py`, is to run it from the repo root — Python adds the script's own directory to `sys.path[0]` automatically, so the import resolves.

**Step 2: Verify operationally**

```bash
cd /var/home/louie/Projects/Volunteering-Consulting/wikipedia-career-images
uv run python analysis/historical-decline/init_db.py
```
Expected output:
```
Initialized /.../analysis/historical-decline/history.db
Tables: annual_totals, ever_top, fetch_log
```

Run it a second time to confirm idempotency:

```bash
uv run python analysis/historical-decline/init_db.py
```
Expected: identical output, no errors.

Confirm the schema is actually applied:

```bash
sqlite3 analysis/historical-decline/history.db ".schema"
```
Expected: the three CREATE TABLE statements and both CREATE INDEX statements.

**Step 3: Commit**

```bash
git add analysis/historical-decline/init_db.py
git commit -m "feat(analysis): add init_db CLI for history.db"
```

---

## Task 5: Ignore history.db from git

**Files:**
- Modify: `.gitignore`

**Step 1: Read current .gitignore**

```bash
cat .gitignore
```

**Step 2: Append the new pattern**

Add this line to `.gitignore` (append at end if there is no existing "analysis" section):

```
# Historical pageview analysis subproject — local research artifact
analysis/historical-decline/history.db
analysis/historical-decline/output/
```

Rationale: `history.db` grows to a few MB after a full fetch and changes monotonically as new months accrue. Treat it as a research artifact, not source.

**Step 3: Verify**

```bash
git status --porcelain analysis/historical-decline/history.db
```
Expected: no output (file is ignored).

```bash
git check-ignore -v analysis/historical-decline/history.db
```
Expected: output pointing at the `.gitignore` line.

**Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore analysis/historical-decline local artifacts"
```

---

## Phase 1 Done Criteria

All of the following must be true before proceeding to Phase 2:

- `uv run python analysis/historical-decline/init_db.py` runs successfully and creates an empty `history.db` with tables `annual_totals`, `ever_top`, `fetch_log`.
- Re-running the same command is a no-op (no errors, no schema changes).
- `git status` is clean (other than the pre-existing unrelated working-tree changes).
- `uv run pytest` still passes on the full existing test suite (should be unaffected — no new tests in this phase, matching the design's operational-verification stance for infrastructure).
