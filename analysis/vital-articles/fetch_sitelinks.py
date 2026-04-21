"""Fetch per-language Wikipedia sitelinks for sampled QIDs.

For each resolved samples.wikidata_id, ask Wikidata for every language-Wikipedia
sitelink (skipping Commons, Wiktionary, Wikiquote, etc.). Stored in the
sitelinks(qid, language, foreign_title) table so coverage_report.py can count
how many sampled articles each language wiki covers.

Batched at 100 QIDs per SPARQL request — each QID has up to ~300 sitelinks,
so larger batches risk hitting the Wikidata SPARQL result-size limit. Sequential
by design, matching the repo's Wikimedia-API convention. Resumable: QIDs that
already have at least one row in sitelinks are skipped on re-run.

Usage:
    uv run python analysis/vital-articles/fetch_sitelinks.py
    uv run python analysis/vital-articles/fetch_sitelinks.py --batch-size 50
    uv run python analysis/vital-articles/fetch_sitelinks.py --refetch
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

import vital_db  # noqa: E402

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = (
    "WikipediaCareerImages-VitalArticles/1.0 "
    "(User:LuisVilla; https://en.wikipedia.org/wiki/User:LuisVilla/ImageDiversityTool)"
)
DEFAULT_BATCH_SIZE = 100
DEFAULT_DELAY = 0.5
REQUEST_TIMEOUT = 180
MAX_RETRIES = 3


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_qids(refetch: bool) -> list[str]:
    if refetch:
        query = (
            "SELECT DISTINCT wikidata_id FROM samples "
            "WHERE wikidata_id IS NOT NULL ORDER BY wikidata_id"
        )
    else:
        query = """
            SELECT DISTINCT s.wikidata_id
            FROM samples s
            LEFT JOIN sitelinks sl ON sl.qid = s.wikidata_id
            WHERE s.wikidata_id IS NOT NULL AND sl.qid IS NULL
            ORDER BY s.wikidata_id
        """
    with vital_db.get_connection() as conn:
        return [r["wikidata_id"] for r in conn.execute(query).fetchall()]


def _insert_sitelinks(rows: list[tuple[str, str, str]]) -> None:
    if not rows:
        return
    with vital_db.get_connection() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO sitelinks (qid, language, foreign_title) "
            "VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()


def _fetch_batch(
    qids: list[str], session: requests.Session
) -> list[tuple[str, str, str]]:
    values = " ".join(f"wd:{q}" for q in qids)
    query = f"""
        SELECT ?item ?lang ?title WHERE {{
          VALUES ?item {{ {values} }}
          ?article schema:about ?item;
                   schema:isPartOf ?site;
                   schema:name ?title;
                   schema:inLanguage ?lang.
          FILTER(CONTAINS(STR(?site), ".wikipedia.org"))
        }}
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.post(
                SPARQL_ENDPOINT,
                data={"query": query},
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/sparql-results+json",
                },
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 429:
                backoff = 2 ** attempt
                log(f"  429 on attempt {attempt}, backing off {backoff}s")
                time.sleep(backoff)
                continue
            r.raise_for_status()
            bindings = r.json()["results"]["bindings"]
            out: list[tuple[str, str, str]] = []
            for b in bindings:
                qid = b["item"]["value"].rsplit("/", 1)[-1]
                lang = b["lang"]["value"]
                title = b["title"]["value"]
                out.append((qid, lang, title))
            return out
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                raise
            backoff = 2 ** attempt
            log(f"  attempt {attempt} failed: {e} — retrying in {backoff}s")
            time.sleep(backoff)
    return []


def fetch_all(batch_size: int, delay: float, refetch: bool) -> None:
    qids = _load_qids(refetch)
    if not qids:
        log("No QIDs needing sitelink fetch.")
        return

    log(f"Fetching sitelinks for {len(qids):,} QIDs "
        f"(batch_size={batch_size})")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        row_total = 0
        for i in range(0, len(qids), batch_size):
            batch = qids[i:i + batch_size]
            try:
                rows = _fetch_batch(batch, session)
            except requests.RequestException as e:
                log(f"  batch {i // batch_size + 1} failed after retries: {e} "
                    f"— skipping {len(batch)} QIDs")
                continue
            _insert_sitelinks(rows)
            row_total += len(rows)
            batch_num = i // batch_size + 1
            total_batches = (len(qids) + batch_size - 1) // batch_size
            log(f"  batch {batch_num}/{total_batches}: "
                f"{len(rows):,} sitelinks for {len(batch)} QIDs  "
                f"(cumulative: {row_total:,})")
            if delay > 0 and i + batch_size < len(qids):
                time.sleep(delay)
    finally:
        session.close()


def _print_summary() -> None:
    with vital_db.get_connection() as conn:
        (total_qids,) = conn.execute(
            "SELECT COUNT(*) FROM samples WHERE wikidata_id IS NOT NULL"
        ).fetchone()
        (covered_qids,) = conn.execute(
            "SELECT COUNT(DISTINCT qid) FROM sitelinks"
        ).fetchone()
        (total_rows,) = conn.execute("SELECT COUNT(*) FROM sitelinks").fetchone()
        (languages,) = conn.execute(
            "SELECT COUNT(DISTINCT language) FROM sitelinks"
        ).fetchone()
    log(f"Done. QIDs with ≥1 sitelink: {covered_qids:,}/{total_qids:,}  "
        f"total sitelink rows: {total_rows:,}  "
        f"distinct languages: {languages:,}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--refetch", action="store_true",
                        help="Re-fetch all QIDs even if sitelinks already stored")
    args = parser.parse_args()

    vital_db.init_schema()
    fetch_all(args.batch_size, args.delay, args.refetch)
    _print_summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
