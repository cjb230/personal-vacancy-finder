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


def test_latest_n_feeds_close_only_after_stale_window(conn):
    class LatestOnly(FakeCollector):
        stale_after_days = 14

    run_collector(conn, LatestOnly("feed", [posting("1")]))
    run_collector(conn, LatestOnly("feed", []))
    assert count(conn, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 1

    conn.execute("UPDATE postings SET last_seen_at = now() - interval '20 days'")
    conn.commit()
    run_collector(conn, LatestOnly("feed", []))
    assert count(conn, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 0


def test_closing_dates_from_source_and_text(conn):
    from datetime import UTC, datetime

    from vacancy_finder.store import ensure_source, fill_deadlines, upsert_posting

    source = ensure_source(conn, "test", "Test board", "board")
    expiry = datetime(2026, 11, 26, 23, 0, tzinfo=UTC)
    upsert_posting(conn, source, RawPosting(
        external_id="1", url="https://x/1", title="A", company_name="Acme",
        description="Apply by 10 November 2026.", posted_at=datetime(2026, 10, 20, tzinfo=UTC),
        closes_at=expiry, closes_kind="expiry"))
    upsert_posting(conn, source, RawPosting(
        external_id="2", url="https://x/2", title="B", company_name="Acme",
        closes_at=expiry, closes_kind="expiry"))
    upsert_posting(conn, source, RawPosting(external_id="3", url="https://x/3", title="C",
                                            company_name="Acme", description="No dates."))
    rows = dict((r[0], r[1:]) for r in conn.execute(
        "SELECT external_id, closes_kind, closes_at FROM postings ORDER BY external_id"))
    # The employer's stated deadline wins over the board's expiry.
    assert rows["1"] == ("deadline", datetime(2026, 11, 10, 22, 59, 59, tzinfo=UTC))
    assert rows["2"] == ("expiry", expiry)
    assert rows["3"] == (None, None)

    # Postings stored before deadlines were read get them on the next start.
    conn.execute("UPDATE postings SET description = 'Closing date: 2026-11-12', closes_kind = NULL,"
                 " closes_at = NULL WHERE external_id = '3'")
    assert fill_deadlines(conn) == 1
    assert conn.execute("SELECT closes_kind FROM postings WHERE external_id = '3'").fetchone() == (
        "deadline",)


def test_job_closing_view(conn):
    from vacancy_finder.store import ensure_source, upsert_posting

    a = ensure_source(conn, "a", "A", "board")
    b = ensure_source(conn, "b", "B", "board")

    def post(source, ext, title, closes_at=None, kind=None):
        upsert_posting(conn, source, RawPosting(external_id=ext, url=f"https://x/{ext}", title=title,
                                                company_name="Acme", closes_at=closes_at,
                                                closes_kind=kind))
    post(a, "1", "Both expire", "2026-11-01T00:00Z", "expiry")
    post(b, "2", "Both expire", "2026-11-05T00:00Z", "expiry")
    post(a, "3", "One never expires", "2026-11-01T00:00Z", "expiry")
    post(b, "4", "One never expires")
    post(a, "5", "Deadline", "2026-11-20T00:00Z", "expiry")
    post(b, "6", "Deadline", "2026-11-03T00:00Z", "deadline")
    rows = dict((title, (str(closes.date()) if closes else None, stated)) for title, closes, stated in conn.execute(
        "SELECT j.title, c.closes_at AT TIME ZONE 'UTC', c.is_deadline FROM jobs j "
        "JOIN job_closing c ON c.job_id = j.id"))
    assert rows == {"Both expire": ("2026-11-05", False), "One never expires": (None, False),
                    "Deadline": ("2026-11-03", True)}
