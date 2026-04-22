"""Fetch current Lift Wing articlequality scores for the 5,000 sampled articles.

Two-stage pipeline:
  1. Resolve each sample title → current revision ID via the MediaWiki Action
     API (batch of 50 titles/request, with redirect following).
  2. Per rev_id, call Lift Wing enwiki-articlequality:predict and parse out
     the class probabilities + predicted class.

Writes a per-title row to the article_quality table with:
  - rev_id, predicted_class
  - prob_{stub,start,c,b,ga,fa} (the full class distribution)
  - expected_quality = Σ(p_i * w_i) with weights Stub=0..FA=5. This is the
    'continuous' quality proxy suitable for correlating against pct_change.

Sequential by design. Resumable: already-'ok' rows are skipped.

Usage:
    uv run python analysis/vital-articles/fetch_quality.py fetch
    uv run python analysis/vital-articles/fetch_quality.py fetch --limit 10
    uv run python analysis/vital-articles/fetch_quality.py resume
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

import vital_db  # noqa: E402

USER_AGENT = (
    "WikipediaCareerImages-VitalArticles/1.0 "
    "(User:LuisVilla; https://en.wikipedia.org/wiki/User:LuisVilla/ImageDiversityTool)"
)
MW_API = "https://en.wikipedia.org/w/api.php"
LIFTWING_URL = (
    "https://api.wikimedia.org/service/lw/inference/v1/"
    "models/enwiki-articlequality:predict"
)
REVID_BATCH = 50
DEFAULT_DELAY = 0.1
PROGRESS_EVERY = 200
MAX_RETRIES = 3
REQUEST_TIMEOUT = 60

CLASS_WEIGHTS = {"Stub": 0, "Start": 1, "C": 2, "B": 3, "GA": 4, "FA": 5}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_targets(refetch: bool) -> list[str]:
    if refetch:
        query = "SELECT title FROM samples ORDER BY title"
    else:
        query = """
            SELECT s.title
            FROM samples s
            LEFT JOIN article_quality aq ON aq.title = s.title
            WHERE aq.title IS NULL OR aq.status != 'ok'
            ORDER BY s.title
        """
    with vital_db.get_connection() as conn:
        return [r["title"] for r in conn.execute(query).fetchall()]


def _fetch_revids(
    titles: list[str], session: requests.Session
) -> dict[str, int]:
    """Batched MW query for current revid per title. Returns {title: revid}.

    Follows redirects; the returned dict may use the canonical (post-redirect)
    title as the key. Callers reconcile by looking up both the requested and
    the canonical title.
    """
    out: dict[str, int] = {}
    for i in range(0, len(titles), REVID_BATCH):
        batch = titles[i:i + REVID_BATCH]
        params = {
            "action": "query",
            "prop": "revisions",
            "rvprop": "ids",
            "titles": "|".join(batch),
            "redirects": 1,
            "format": "json",
            "formatversion": 2,
        }
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = session.get(MW_API, params=params, timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                data = r.json()
                break
            except requests.RequestException as e:
                if attempt == MAX_RETRIES:
                    log(f"  revid batch {i // REVID_BATCH + 1} failed after retries: {e}")
                    data = None
                    break
                time.sleep(2 ** attempt)
        if not data:
            continue

        # Build forward index so both input and canonical titles resolve.
        rename: dict[str, str] = {}
        for entry in data.get("query", {}).get("normalized", []):
            rename[entry["from"]] = entry["to"]
        for entry in data.get("query", {}).get("redirects", []):
            rename[entry["from"]] = entry["to"]
        page_by_title: dict[str, dict] = {}
        for page in data.get("query", {}).get("pages", []):
            page_by_title[page["title"]] = page

        for input_title in batch:
            canonical = rename.get(input_title, input_title)
            page = page_by_title.get(canonical)
            if not page or "revisions" not in page:
                continue
            revs = page["revisions"]
            if not revs:
                continue
            out[input_title] = revs[0]["revid"]
        time.sleep(0.1)
    return out


def _parse_liftwing(payload: dict, rev_id: int) -> dict | None:
    """Return {prediction, probability: {class: p}} or None on malformed."""
    try:
        score = payload["enwiki"]["scores"][str(rev_id)]["articlequality"]["score"]
        return {
            "prediction": score["prediction"],
            "probability": score["probability"],
        }
    except (KeyError, TypeError):
        return None


def _fetch_quality_one(
    session: requests.Session, rev_id: int
) -> tuple[str, dict | None, str | None]:
    """Returns (status, parsed_score_or_None, error_msg).

    status ∈ {'ok', 'model_error', 'http_error'}.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.post(
                LIFTWING_URL,
                json={"rev_id": rev_id},
                headers={"Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                return "http_error", None, str(e)
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429 or 500 <= r.status_code < 600:
            if attempt == MAX_RETRIES:
                return "http_error", None, f"status={r.status_code}"
            time.sleep(2 ** attempt)
            continue
        if r.status_code != 200:
            return "http_error", None, f"status={r.status_code}: {r.text[:200]}"
        try:
            payload = r.json()
        except ValueError as e:
            return "http_error", None, f"invalid json: {e}"
        parsed = _parse_liftwing(payload, rev_id)
        if parsed is None:
            return "model_error", None, f"unexpected payload: {str(payload)[:200]}"
        return "ok", parsed, None
    return "http_error", None, "retries exhausted"


def _write_row(
    title: str, rev_id: int | None, status: str,
    parsed: dict | None, error: str | None,
) -> None:
    probs = (parsed or {}).get("probability", {})
    expected = (
        sum(CLASS_WEIGHTS[c] * probs.get(c, 0.0) for c in CLASS_WEIGHTS)
        if parsed is not None else None
    )
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    with vital_db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO article_quality
              (title, rev_id, predicted_class, expected_quality,
               prob_stub, prob_start, prob_c, prob_b, prob_ga, prob_fa,
               fetched_at, status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(title) DO UPDATE SET
              rev_id = excluded.rev_id,
              predicted_class = excluded.predicted_class,
              expected_quality = excluded.expected_quality,
              prob_stub = excluded.prob_stub,
              prob_start = excluded.prob_start,
              prob_c = excluded.prob_c,
              prob_b = excluded.prob_b,
              prob_ga = excluded.prob_ga,
              prob_fa = excluded.prob_fa,
              fetched_at = excluded.fetched_at,
              status = excluded.status,
              error = excluded.error
            """,
            (
                title, rev_id,
                (parsed or {}).get("prediction"),
                expected,
                probs.get("Stub"), probs.get("Start"), probs.get("C"),
                probs.get("B"), probs.get("GA"), probs.get("FA"),
                fetched_at, status, error,
            ),
        )
        conn.commit()


def run(delay: float, refetch: bool, limit: int | None) -> None:
    titles = _load_targets(refetch)
    if limit is not None:
        titles = titles[:limit]
    if not titles:
        log("Nothing to do.")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        log(f"Stage 1: resolving {len(titles):,} titles → current rev_ids")
        revid_map = _fetch_revids(titles, session)
        log(f"  resolved {len(revid_map):,}/{len(titles):,}")

        missing_revid = [t for t in titles if t not in revid_map]
        for t in missing_revid:
            _write_row(t, None, "missing_revid", None, None)

        log(f"Stage 2: scoring {len(revid_map):,} articles via Lift Wing")
        resolved_titles = [t for t in titles if t in revid_map]
        total = len(resolved_titles)
        ok_n = 0
        for i, title in enumerate(resolved_titles, 1):
            rev_id = revid_map[title]
            status, parsed, err = _fetch_quality_one(session, rev_id)
            _write_row(title, rev_id, status, parsed, err)
            if status == "ok":
                ok_n += 1
            if i % PROGRESS_EVERY == 0 or i == total:
                log(f"  progress: {i}/{total} ({i * 100 // total}%)  ok so far: {ok_n:,}")
            if delay > 0 and i < total:
                time.sleep(delay)
    finally:
        session.close()

    _print_summary()


def _print_summary() -> None:
    with vital_db.get_connection() as conn:
        (total,) = conn.execute("SELECT COUNT(*) FROM samples").fetchone()
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM article_quality GROUP BY status"
        ).fetchall()
        class_rows = conn.execute(
            "SELECT predicted_class, COUNT(*) AS n FROM article_quality "
            "WHERE status = 'ok' GROUP BY predicted_class "
            "ORDER BY CASE predicted_class "
            "  WHEN 'Stub' THEN 0 WHEN 'Start' THEN 1 WHEN 'C' THEN 2 "
            "  WHEN 'B' THEN 3 WHEN 'GA' THEN 4 WHEN 'FA' THEN 5 END"
        ).fetchall()
    log(f"Done. total samples: {total:,}")
    for r in status_rows:
        log(f"  status={r['status']:<14} {r['n']:,}")
    log("Predicted class breakdown (ok only):")
    for r in class_rows:
        log(f"  {r['predicted_class']:<5} {r['n']:,}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("fetch", "resume"):
        p = sub.add_parser(name)
        p.add_argument("--delay", type=float, default=DEFAULT_DELAY)
        p.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    vital_db.init_schema()
    run(args.delay, refetch=(args.cmd == "fetch"), limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
