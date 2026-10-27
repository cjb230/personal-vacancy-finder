"""Database tests. They run only when TEST_DATABASE_URL points at an empty, disposable Postgres."""

import os
from pathlib import Path

import psycopg
import pytest

from vacancy_finder.migrate import apply_migrations
from vacancy_finder.models import RawPosting
from vacancy_finder.runner import run_collector

DB_URL = os.environ.get("TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def conn():
    with psycopg.connect(DB_URL) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        c.commit()
        apply_migrations(c, MIGRATIONS)
        yield c


class FakeCollector:
    source_kind = "board"

    def __init__(self, key, postings):
        self.source_key = key
        self.source_name = key
        self.postings = postings

    def collect(self, conn):
        yield from self.postings

    def incomplete_company_ids(self):
        return set()


def posting(external_id, company="ICEYE Oy", title="Staff Software Engineer"):
    return RawPosting(
        external_id=external_id,
        url=f"https://example.com/{external_id}",
        title=title,
        company_name=company,
    )


def count(conn, sql):
    return conn.execute(sql).fetchone()[0]


def test_migrations_apply_once(conn):
    assert apply_migrations(conn, MIGRATIONS) == []


def test_same_job_on_two_sources_is_one_job(conn):
    run_collector(conn, FakeCollector("board_a", [posting("1")]))
    run_collector(conn, FakeCollector("board_b", [posting("x9", company="ICEYE", title="Staff Software Engineer (m/f/d)")]))
    assert count(conn, "SELECT count(*) FROM postings") == 2
    assert count(conn, "SELECT count(*) FROM jobs") == 1
    assert count(conn, "SELECT count(*) FROM companies") == 1


def test_rerun_updates_instead_of_duplicating(conn):
    collector = FakeCollector("board_a", [posting("1")])
    run_collector(conn, collector)
    run_collector(conn, collector)
    assert count(conn, "SELECT count(*) FROM postings") == 1
    runs = conn.execute(
        "SELECT postings_seen, postings_new FROM collection_runs ORDER BY id"
    ).fetchall()
    assert runs == [(1, 1), (1, 0)]


def test_postings_that_disappear_are_closed(conn):
    run_collector(conn, FakeCollector("board_a", [posting("1"), posting("2", title="Engineering Manager")]))
    run_collector(conn, FakeCollector("board_a", [posting("1")]))
    assert count(conn, "SELECT count(*) FROM postings WHERE closed_at IS NOT NULL") == 1
    assert count(conn, "SELECT count(*) FROM jobs WHERE status = 'closed'") == 1


def test_job_stays_open_while_another_source_lists_it(conn):
    run_collector(conn, FakeCollector("board_a", [posting("1")]))
    run_collector(conn, FakeCollector("board_b", [posting("b1")]))
    run_collector(conn, FakeCollector("board_a", []))
    assert count(conn, "SELECT count(*) FROM jobs WHERE status = 'open'") == 1


def test_failed_run_is_recorded_and_closes_nothing(conn):
    run_collector(conn, FakeCollector("board_a", [posting("1")]))

    class Broken(FakeCollector):
        def collect(self, conn):
            raise RuntimeError("site down")

    run_collector(conn, Broken("board_a", []))
    assert count(conn, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 1
    status, error = conn.execute(
        "SELECT status, error FROM collection_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert status == "failed" and "site down" in error
