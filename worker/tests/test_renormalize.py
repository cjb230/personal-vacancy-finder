"""Re-keying stored companies and jobs when the matching rules change."""

import pytest

from vacancy_finder.models import RawPosting
from vacancy_finder.renormalize import renormalize
from vacancy_finder.store import close_unseen, ensure_source, upsert_posting

from .test_store import DB_URL, conn, count  # noqa: F401  (conn is a fixture)

pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


def posting(external_id, company, title="Engineering Manager"):
    return RawPosting(external_id=external_id, url=f"https://x.example/{external_id}",
                      title=title, company_name=company)


def test_old_keys_are_merged(conn):  # noqa: F811
    source = ensure_source(conn, "test", "Test", "board")
    upsert_posting(conn, source, posting("1", "Duck Duck Go"))
    upsert_posting(conn, source, posting("2", "DuckDuckGo"))
    conn.commit()
    assert count(conn, "SELECT count(*) FROM jobs") == 1  # already one key under the new rules
    # Simulate rows written under the old rules: two companies with spaced keys, two jobs.
    conn.execute("UPDATE companies SET normalized_name = 'old:' || id")
    conn.execute(
        """
        INSERT INTO companies (name, normalized_name, ats_kind, ats_slug, tracked, sectors)
        VALUES ('Duck Duck Go Sp. z o.o.', 'duck duck go', 'greenhouse', 'ddg', true, '{search}')
        """
    )
    company = conn.execute("SELECT id FROM companies WHERE ats_slug = 'ddg'").fetchone()[0]
    conn.execute(
        "INSERT INTO jobs (company_id, title, fingerprint) VALUES (%s, 'Engineering Manager', 'old')",
        (company,),
    )
    job = conn.execute("SELECT id FROM jobs WHERE fingerprint = 'old'").fetchone()[0]
    conn.execute("UPDATE postings SET job_id = %s WHERE external_id = '2'", (job,))
    conn.commit()

    assert renormalize(conn) == (1, 1)
    assert conn.execute("SELECT normalized_name, ats_slug, tracked, sectors FROM companies").fetchall() == [
        ("duckduckgo", "ddg", True, ["search"])
    ]
    assert count(conn, "SELECT count(*) FROM jobs") == 1
    assert count(conn, "SELECT count(DISTINCT job_id) FROM postings") == 1
    assert renormalize(conn) == (0, 0)  # nothing left to do


def test_unknown_company_and_placeholder_titles_stay_separate(conn):  # noqa: F811
    source = ensure_source(conn, "test", "Test", "board")
    upsert_posting(conn, source, posting("1", "(unknown)"))
    upsert_posting(conn, source, posting("2", "(unknown)"))
    hn = posting("3", "Acme", "Open roles (see post)")
    hn.unique_job = True
    upsert_posting(conn, source, hn)
    conn.commit()
    assert count(conn, "SELECT count(*) FROM jobs") == 3
    assert renormalize(conn) == (0, 0)
    assert count(conn, "SELECT count(*) FROM jobs") == 3


def test_jobs_without_postings_are_removed(conn):  # noqa: F811
    from datetime import UTC, datetime

    source = ensure_source(conn, "test", "Test", "board")
    upsert_posting(conn, source, posting("1", "Acme", "Old title"))
    # The same posting is later re-keyed to another job, leaving the first without postings.
    upsert_posting(conn, source, posting("1", "Acme", "New title"))
    close_unseen(conn, source, datetime(2000, 1, 1, tzinfo=UTC))
    conn.commit()
    assert conn.execute("SELECT title FROM jobs").fetchall() == [("New title",)]


def test_companies_without_jobs_are_removed_unless_seeded(conn):  # noqa: F811
    from datetime import UTC, datetime

    source = ensure_source(conn, "test", "Test", "board")
    upsert_posting(conn, source, posting("1", "Beacon AI builds intelligent systems"))
    upsert_posting(conn, source, posting("1", "Beacon AI"))
    conn.execute(
        "INSERT INTO companies (name, normalized_name, tracked, ats_kind, ats_slug) "
        "VALUES ('Seeded', 'seeded', true, 'ashby', 'seeded')"
    )
    close_unseen(conn, source, datetime(2000, 1, 1, tzinfo=UTC))
    conn.commit()
    assert conn.execute("SELECT name FROM companies ORDER BY name").fetchall() == [
        ("Beacon AI",), ("Seeded",)
    ]
