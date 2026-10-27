"""Scoring stored jobs and the results page, against a real database."""

import os

import pytest

from vacancy_finder import web
from vacancy_finder.matching import score_all
from vacancy_finder.models import RawPosting
from vacancy_finder.store import ensure_source, upsert_posting

from .test_store import DB_URL, conn, count  # noqa: F401  (conn is a fixture)

pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


def test_score_render_and_review(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    source = ensure_source(conn, "test", "Test board", "board")
    upsert_posting(conn, source, RawPosting(
        external_id="1", url="https://x.example/1", title="Engineering Manager <Python>",
        company_name="Orbit & Co", location_text="Remote, Europe", work_mode="remote",
        description="Python and PostgreSQL for satellite imagery. You will lead the team."))
    upsert_posting(conn, source, RawPosting(
        external_id="2", url="https://x.example/2", title="Account Executive",
        company_name="Acme", work_mode="remote", description="Sell things to people we know."))
    conn.commit()

    assert score_all(conn) == {"match": 1, "no": 1}
    page = web.render("match", "", 0)
    assert "Engineering Manager &lt;Python&gt;" in page  # escaped
    assert "Matches (1)" in page and "Filtered out (1)" in page

    job_id = conn.execute("SELECT id FROM jobs WHERE title LIKE 'Engineering%'").fetchone()[0]
    web.save_review(job_id, "interested")
    assert "Matches (0)" in web.render("match", "", 0)
    assert "Engineering Manager" in web.render("interested", "", 0)
    assert "Account Executive" in web.render("no", "acme", 0)
    web.save_review(job_id, "")
    assert count(conn, "SELECT count(*) FROM job_reviews") == 0


def test_feedback_keeps_a_snapshot_of_the_ad(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    source = ensure_source(conn, "test", "Test board", "board")
    upsert_posting(conn, source, RawPosting(
        external_id="1", url="https://x.example/1", title="Engineering Manager",
        company_name="Orbit", location_text="Remote, Europe", work_mode="remote",
        description="Python and PostgreSQL for satellite imagery. You will lead the team."))
    conn.commit()
    score_all(conn)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]

    web.save_feedback(job_id, "rate_higher", "space & GIS")
    web.save_feedback(job_id, "score_lower")
    page = web.render("match", "", 0)
    assert "Rate higher: space &amp; GIS" in page and "Score lower" in page
    assert "Reject for:" in page

    web.save_feedback(job_id, "reject", "needs German")
    assert "Matches (0)" in web.render("match", "", 0)
    assert "Rejected: needs German" in web.render("not_interested", "", 0)

    # The snapshot outlives later changes to the job and its postings.
    conn.execute("UPDATE postings SET description = 'changed'")
    conn.commit()
    ad_text, postings, score, reasons = conn.execute(
        "SELECT ad_text, postings, score, reasons FROM job_feedback WHERE kind = 'reject'").fetchone()
    assert ad_text.startswith("Python and PostgreSQL")
    assert postings[0]["url"] == "https://x.example/1" and postings[0]["description"] == ad_text
    assert score > 0 and reasons

    feedback_id = conn.execute("SELECT id FROM job_feedback WHERE kind = 'score_lower'").fetchone()[0]
    web.delete_feedback(feedback_id)
    assert count(conn, "SELECT count(*) FROM job_feedback") == 2
