"""Rank computation and ever-top union for historical pageview analysis.

Operates on history.db (populated by fetch_history.py). Pure-SQL plus a small
amount of Python orchestration.
"""
from __future__ import annotations

from pathlib import Path

import history_db


def compute_ranks(db_path: Path | str = history_db.DEFAULT_DB_PATH) -> None:
    """Compute per-year dense rank (with deterministic tie-break) over annual_totals.

    Writes the result back into annual_totals.rank. Idempotent.
    """
    with history_db.get_connection(db_path) as conn:
        conn.execute(
            """
            WITH ranked AS (
                SELECT
                    wikidata_id,
                    year,
                    DENSE_RANK() OVER (
                        PARTITION BY year
                        ORDER BY views DESC, wikidata_id ASC
                    ) AS computed_rank
                FROM annual_totals
            )
            UPDATE annual_totals
            SET rank = (
                SELECT computed_rank FROM ranked
                WHERE ranked.wikidata_id = annual_totals.wikidata_id
                  AND ranked.year = annual_totals.year
            )
            """
        )
        conn.commit()


def compute_ever_top(
    top_n: int,
    db_path: Path | str = history_db.DEFAULT_DB_PATH,
) -> None:
    """Compute the ever_top table: articles that ranked <= top_n in any year.

    Replaces any previous ever_top contents. Requires compute_ranks() to have
    been run first (reads annual_totals.rank).
    """
    with history_db.get_connection(db_path) as conn:
        conn.execute("DELETE FROM ever_top")
        conn.execute(
            """
            INSERT INTO ever_top (
                wikidata_id, title, first_top_year, last_top_year,
                years_in_top, peak_rank, peak_year
            )
            SELECT
                t.wikidata_id,
                -- Use the most recent title we have for this article.
                (SELECT title FROM annual_totals
                    WHERE wikidata_id = t.wikidata_id
                    ORDER BY year DESC LIMIT 1) AS title,
                MIN(t.year) AS first_top_year,
                MAX(t.year) AS last_top_year,
                COUNT(*) AS years_in_top,
                MIN(t.rank) AS peak_rank,
                -- peak_year: year where the article achieved its peak_rank.
                -- If ties across years, the earliest such year.
                (SELECT MIN(year) FROM annual_totals a2
                    WHERE a2.wikidata_id = t.wikidata_id
                      AND a2.rank = MIN(t.rank)) AS peak_year
            FROM annual_totals t
            WHERE t.rank IS NOT NULL AND t.rank <= ?
            GROUP BY t.wikidata_id
            """,
            (top_n,),
        )
        conn.commit()
