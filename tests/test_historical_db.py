"""Tests for history_db write helpers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import history_db


@pytest.fixture
def fresh_db(tmp_path):
    db_path = tmp_path / "history.db"
    history_db.init_schema(db_path)
    return db_path


class TestUpsertAnnualTotals:
    def test_inserts_rows(self, fresh_db):
        rows = [
            ("Q123", "Surgeon", 2016, 12345),
            ("Q123", "Surgeon", 2017, 10000),
        ]
        history_db.upsert_annual_totals(rows, db_path=fresh_db)
        with history_db.get_connection(fresh_db) as conn:
            result = conn.execute(
                "SELECT wikidata_id, year, views FROM annual_totals ORDER BY year"
            ).fetchall()
        assert len(result) == 2
        assert result[0]["views"] == 12345
        assert result[1]["views"] == 10000

    def test_replaces_on_conflict(self, fresh_db):
        history_db.upsert_annual_totals(
            [("Q123", "Surgeon", 2016, 100)], db_path=fresh_db
        )
        history_db.upsert_annual_totals(
            [("Q123", "Surgeon", 2016, 999)], db_path=fresh_db
        )
        with history_db.get_connection(fresh_db) as conn:
            (views,) = conn.execute(
                "SELECT views FROM annual_totals WHERE wikidata_id='Q123' AND year=2016"
            ).fetchone()
        assert views == 999


class TestUpsertMonthlyViews:
    def test_inserts_rows(self, fresh_db):
        rows = [
            ("Q123", "Surgeon", 2016, 1, 100),
            ("Q123", "Surgeon", 2016, 2, 200),
            ("Q123", "Surgeon", 2026, 3, 50),
        ]
        history_db.upsert_monthly_views(rows, db_path=fresh_db)
        with history_db.get_connection(fresh_db) as conn:
            result = conn.execute(
                "SELECT year, month, views FROM monthly_views "
                "WHERE wikidata_id='Q123' ORDER BY year, month"
            ).fetchall()
        assert len(result) == 3
        assert (result[0]["year"], result[0]["month"], result[0]["views"]) == (2016, 1, 100)
        assert (result[2]["year"], result[2]["month"], result[2]["views"]) == (2026, 3, 50)

    def test_replaces_on_conflict(self, fresh_db):
        history_db.upsert_monthly_views(
            [("Q123", "Surgeon", 2016, 1, 100)], db_path=fresh_db
        )
        history_db.upsert_monthly_views(
            [("Q123", "Surgeon", 2016, 1, 999)], db_path=fresh_db
        )
        with history_db.get_connection(fresh_db) as conn:
            (views,) = conn.execute(
                "SELECT views FROM monthly_views "
                "WHERE wikidata_id='Q123' AND year=2016 AND month=1"
            ).fetchone()
        assert views == 999


class TestFetchLog:
    def test_records_ok_status(self, fresh_db):
        history_db.record_fetch_status(
            "Q123", "Surgeon", "ok", error=None, db_path=fresh_db
        )
        with history_db.get_connection(fresh_db) as conn:
            row = conn.execute(
                "SELECT wikidata_id, status, error FROM fetch_log"
            ).fetchone()
        assert row["wikidata_id"] == "Q123"
        assert row["status"] == "ok"
        assert row["error"] is None

    def test_records_error_with_message(self, fresh_db):
        history_db.record_fetch_status(
            "Q456", "Nonexistent", "error", error="timeout", db_path=fresh_db
        )
        with history_db.get_connection(fresh_db) as conn:
            row = conn.execute(
                "SELECT status, error FROM fetch_log WHERE wikidata_id='Q456'"
            ).fetchone()
        assert row["status"] == "error"
        assert row["error"] == "timeout"

    def test_rejects_invalid_status(self, fresh_db):
        # CHECK constraint in schema should reject this
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            history_db.record_fetch_status(
                "Q789", "Foo", "bogus", error=None, db_path=fresh_db
            )

    def test_get_qids_needing_fetch_excludes_ok(self, fresh_db):
        history_db.record_fetch_status("Q1", "A", "ok", None, db_path=fresh_db)
        history_db.record_fetch_status("Q2", "B", "error", "x", db_path=fresh_db)
        history_db.record_fetch_status("Q3", "C", "missing", None, db_path=fresh_db)
        all_qids = {"Q1", "Q2", "Q3", "Q4"}
        needing = history_db.get_qids_needing_fetch(all_qids, db_path=fresh_db)
        assert needing == {"Q2", "Q3", "Q4"}
