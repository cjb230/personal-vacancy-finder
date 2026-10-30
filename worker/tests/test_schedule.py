import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from vacancy_finder.config import Settings
from vacancy_finder.migrate import apply_migrations
from vacancy_finder.runner import run_collector
from vacancy_finder.schedule import due_now, due_times, interval_for, last_started

SETTINGS = Settings(
    database_url="",
    email_interval_minutes=15,
    board_interval_minutes=120,
    ats_interval_minutes=360,
    migrations_dir=Path(),
    seeds_file=Path(),
    log_level="INFO",
)
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@dataclass
class Fake:
    source_key: str
    source_kind: str
    source_name: str = "fake"

    def collect(self, conn):
        return []

    def incomplete_company_ids(self):
        return set()


ATS = Fake("greenhouse", "ats")
BOARD = Fake("justjoinit", "board")
EMAIL = Fake("email_alerts", "email")


def test_intervals_by_kind():
    assert interval_for(EMAIL, SETTINGS) == timedelta(minutes=15)
    assert interval_for(BOARD, SETTINGS) == timedelta(hours=2)
    assert interval_for(ATS, SETTINGS) == timedelta(hours=6)
    assert interval_for(Fake("x", "custom"), SETTINGS) == timedelta(hours=6)


def test_never_run_is_due_email_first():
    assert due_now([ATS, BOARD, EMAIL], SETTINGS, {}, NOW) == [EMAIL, BOARD, ATS]


def test_each_source_keeps_its_own_clock():
    last = {
        "email_alerts": NOW - timedelta(minutes=16),
        "justjoinit": NOW - timedelta(minutes=90),
        "greenhouse": NOW - timedelta(hours=7),
    }
    assert due_now([ATS, BOARD, EMAIL], SETTINGS, last, NOW) == [EMAIL, ATS]
    assert due_times([BOARD], SETTINGS, last, NOW) == {"justjoinit": NOW + timedelta(minutes=30)}


def test_nothing_due_right_after_a_run():
    last = {c.source_key: NOW for c in (ATS, BOARD, EMAIL)}
    assert due_now([ATS, BOARD, EMAIL], SETTINGS, last, NOW) == []


DB_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")
def test_last_started_reads_latest_run_per_source():
    with psycopg.connect(DB_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
        apply_migrations(conn, Path(__file__).resolve().parents[1] / "migrations")
        assert last_started(conn) == {}
        run_collector(conn, BOARD)
        run_collector(conn, BOARD)
        run_collector(conn, EMAIL)
        last = last_started(conn)
        assert set(last) == {"justjoinit", "email_alerts"}
        latest = conn.execute(
            "SELECT max(started_at) FROM collection_runs r JOIN sources s ON s.id = r.source_id"
            " WHERE s.key = 'justjoinit'"
        ).fetchone()[0]
        assert last["justjoinit"] == latest
