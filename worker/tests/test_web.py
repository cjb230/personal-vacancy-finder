"""Scoring stored jobs and the results page, against a real database."""

import os
from datetime import datetime

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


def test_filters_by_first_seen_and_score(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    source = ensure_source(conn, "test", "Test board", "board")
    for n, title in (("1", "Engineering Manager"), ("2", "Staff Python Engineer")):
        upsert_posting(conn, source, RawPosting(
            external_id=n, url=f"https://x.example/{n}", title=title, company_name="Orbit",
            location_text="Remote, Europe", work_mode="remote",
            description="Python and PostgreSQL for satellite imagery. You will lead the team."))
    conn.execute("UPDATE jobs SET first_seen_at = '2026-01-10 09:30+00' WHERE title LIKE 'Staff%'")
    conn.commit()
    score_all(conn)
    old_score = conn.execute(
        "SELECT score FROM job_scores s JOIN jobs j ON j.id = s.job_id WHERE j.title LIKE 'Staff%'"
    ).fetchone()[0]

    everything = web.render("match", "", 0)
    assert "Engineering Manager" in everything and "Staff Python Engineer" in everything
    assert "first seen 10 Jan 10:30" in everything  # shown in Warsaw time

    recent = web.render("match", "", 0, seen="24")
    assert "Engineering Manager" in recent and "Staff Python Engineer" not in recent
    assert "Matches (1)" in recent and "seen=24" in recent  # tabs keep the filter
    since = web.render("match", "", 0, since="2026-01-10T10:00")  # Warsaw, so 09:00 UTC
    assert "Staff Python Engineer" in since
    assert "Staff Python Engineer" not in web.render("match", "", 0, since="2026-01-10T11:00")

    assert "Staff Python Engineer" not in web.render("match", "", 0, min_score=str(old_score + 100))
    newest = web.render("match", "", 0, sort="new")
    assert newest.index("Engineering Manager") < newest.index("Staff Python Engineer")
    assert "Matches (2)" in web.render("match", "", 0, seen="bogus", since="nope", min_score="x")


def test_closing_filter_and_sort(conn, monkeypatch):  # noqa: F811
    from datetime import UTC, datetime, timedelta

    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    source = ensure_source(conn, "test", "Test board", "board")
    now = datetime.now(UTC)
    for n, title, closes, kind in (
            ("1", "Engineering Manager", now + timedelta(hours=30), "deadline"),
            ("2", "Staff Python Engineer", now + timedelta(days=5), "expiry"),
            ("3", "Python Engineering Lead", now - timedelta(days=1), "deadline"),
            ("4", "Head of Engineering", None, None)):
        upsert_posting(conn, source, RawPosting(
            external_id=n, url=f"https://x.example/{n}", title=title, company_name=f"Orbit {n}",
            location_text="Remote, Europe", work_mode="remote", closes_at=closes, closes_kind=kind,
            description="Python and PostgreSQL for satellite imagery. You will lead the team."))
    conn.commit()
    score_all(conn)

    everything = web.render("match", "", 0)
    assert '<span class="soon">apply by' in everything
    assert "ad ends" in everything and "deadline passed" in everything

    soon = web.render("match", "", 0, closes="2")
    assert "Engineering Manager" in soon and "Matches (1)" in soon and "closes=2" in soon
    assert "Staff Python Engineer" not in soon and "Python Engineering Lead" not in soon
    week = web.render("match", "", 0, closes="7")
    assert "Matches (2)" in week and "Head of Engineering" not in week
    assert "Matches (1)" in web.render("match", "", 0, closes="deadline")
    assert "Matches (4)" in web.render("match", "", 0, closes="bogus")

    by_closing = web.render("match", "", 0, sort="closing")
    order = [by_closing.index(t) for t in ("Engineering Manager", "Staff Python Engineer",
                                           "Head of Engineering")]
    assert order == sorted(order)


def test_applied_records_when_and_where(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    source = ensure_source(conn, "test", "Test board", "board")
    upsert_posting(conn, source, RawPosting(
        external_id="1", url="https://x.example/1", title="Engineering Manager",
        company_name="Orbit", location_text="Remote, Europe", work_mode="remote",
        description="Python and PostgreSQL for satellite imagery. You will lead the team."))
    conn.commit()
    score_all(conn)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]

    page = web.render("match", "", 0)
    assert f'id="apply-{job_id}"' in page and 'data-fresh="1"' in page
    assert f'onclick="openApply({job_id})"' in page

    web.save_review(job_id, "applied", datetime(2026, 10, 30, 2, 45), "~/Jobs/Orbit & Co")
    applied = web.render("applied", "", 0)
    assert "Applied 30 Oct 2026 02:45" in applied  # Warsaw time, as entered
    assert "<code>~/Jobs/Orbit &amp; Co</code>" in applied
    assert 'value="2026-10-30T02:45"' in applied and "data-fresh" not in applied
    stored = conn.execute("SELECT applied_at AT TIME ZONE 'UTC' FROM job_reviews").fetchone()[0]
    assert stored == datetime(2026, 10, 30, 1, 45)  # CET is UTC+1 after the clocks change

    # Another decision keeps the details; applying again without a time means now.
    web.save_review(job_id, "interested")
    assert conn.execute("SELECT application_dir FROM job_reviews").fetchone()[0] == "~/Jobs/Orbit & Co"
    web.save_review(job_id, "applied")
    assert "no folder given" in web.render("applied", "", 0)
    assert conn.execute("SELECT applied_at > now() - interval '1 minute' FROM job_reviews").fetchone()[0]


def test_email_jobs_name_the_alert_sender(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    source = ensure_source(conn, "email_alerts", "Job-alert emails", "email")
    upsert_posting(conn, source, RawPosting(
        external_id="linkedin:1", url="https://www.linkedin.com/jobs/view/1/",
        title="Engineering Manager", company_name="Orbit & Co", location_text="Remote, Europe",
        work_mode="remote", description="Python and PostgreSQL for satellite imagery.",
        raw={"platform": "linkedin", "alert_from": "LinkedIn"}))
    conn.commit()
    score_all(conn)
    page = "".join(web.render(view, "", 0) for view in web.VIEWS)
    assert "LinkedIn alert" in page and "Orbit &amp; Co" in page
    assert "Job-alert emails" not in page
