"""Resolve sampled en.wikipedia titles → Wikidata QIDs.

Writes results back to samples.wikidata_id. Titles that don't resolve
(redirects, disambig, deleted pages, rename-in-flight) stay NULL and are
counted in the final report. Resumable: already-resolved rows are skipped.

SPARQL pattern batched at 500 titles per request, same shape as
fetcher.py:fetch_occupation_details. Sequential by design — matches the
repo's "sync + small delay" convention for Wikimedia APIs.

Usage:
    uv run python analysis/vital-articles/fetch_qids.py
    uv run python analysis/vital-articles/fetch_qids.py --batch-size 250
    uv run python analysis/vital-articles/fetch_qids.py --refetch
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
DEFAULT_BATCH_SIZE = 500
DEFAULT_DELAY = 0.5
REQUEST_TIMEOUT = 120


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _escape_sparql_literal(s: str) -> str:
    """Escape backslashes and double quotes for SPARQL string literals."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _load_unresolved_titles(refetch: bool) -> list[str]:
    where = "" if refetch else "WHERE wikidata_id IS NULL"
    query = f"SELECT title FROM samples {where} ORDER BY title"
    with vital_db.get_connection() as conn:
        return [r["title"] for r in conn.execute(query).fetchall()]


def _write_qids(pairs: list[tuple[str, str]]) -> None:
    if not pairs:
        return
    with vital_db.get_connection() as conn:
        conn.executemany(
            "UPDATE samples SET wikidata_id = ? WHERE title = ?",
            [(qid, title) for (title, qid) in pairs],
        )
        conn.commit()


def _resolve_batch(titles: list[str], session: requests.Session) -> dict[str, str]:
    values = " ".join(f'"{_escape_sparql_literal(t)}"@en' for t in titles)
    query = f"""
        SELECT ?item ?enwikiTitle WHERE {{
          VALUES ?enwikiTitle {{ {values} }}
          ?enwiki schema:about ?item;
                  schema:isPartOf <https://en.wikipedia.org/>;
                  schema:name ?enwikiTitle.
        }}
    """
    r = session.post(
        SPARQL_ENDPOINT,
        data={"query": query},
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/sparql-results+json",
        },
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    bindings = r.json()["results"]["bindings"]

    out: dict[str, str] = {}
    for b in bindings:
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        title = b["enwikiTitle"]["value"]
        out.setdefault(title, qid)
    return out


def resolve_all(batch_size: int, delay: float, refetch: bool) -> None:
    titles = _load_unresolved_titles(refetch)
    if not titles:
        log("No unresolved titles remaining.")
        return

    total_sampled = _count_sampled()
    log(f"Resolving {len(titles):,} of {total_sampled:,} sampled titles "
        f"(batch_size={batch_size})")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        resolved_total = 0
        for i in range(0, len(titles), batch_size):
            batch = titles[i:i + batch_size]
            try:
                mapping = _resolve_batch(batch, session)
            except requests.RequestException as e:
                log(f"  batch {i // batch_size + 1} failed: {e}")
                time.sleep(delay * 4)
                continue
            pairs = [(title, mapping[title]) for title in batch if title in mapping]
            _write_qids(pairs)
            resolved_total += len(pairs)
            log(f"  batch {i // batch_size + 1}: "
                f"{len(pairs)}/{len(batch)} resolved  "
                f"(cumulative: {resolved_total:,})")
            if delay > 0 and i + batch_size < len(titles):
                time.sleep(delay)
    finally:
        session.close()


def _count_sampled() -> int:
    with vital_db.get_connection() as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM samples").fetchone()
    return n


def _print_summary() -> None:
    with vital_db.get_connection() as conn:
        (total,) = conn.execute("SELECT COUNT(*) FROM samples").fetchone()
        (resolved,) = conn.execute(
            "SELECT COUNT(*) FROM samples WHERE wikidata_id IS NOT NULL"
        ).fetchone()
    unresolved = total - resolved
    log(f"Done. resolved={resolved:,}  unresolved={unresolved:,}  "
        f"total={total:,}  ({resolved * 100.0 / total:.1f}% coverage)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--refetch", action="store_true",
                        help="Re-resolve all titles, overwriting existing QIDs")
    args = parser.parse_args()

    vital_db.init_schema()
    resolve_all(args.batch_size, args.delay, args.refetch)
    _print_summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
