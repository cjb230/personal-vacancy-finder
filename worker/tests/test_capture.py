"""LinkedIn advert text sent from James's browser, and borrowing text from the same job
listed elsewhere."""

import os

import pytest

from vacancy_finder import web
from vacancy_finder.capture import bookmarklet, clean_description, external_id_for
from vacancy_finder.matching import JobFacts, borrow_twin_descriptions, score_all, title_similarity
from vacancy_finder.models import RawPosting
from vacancy_finder.store import ensure_source, upsert_posting

from .test_store import DB_URL, conn  # noqa: F401  (conn is a fixture)

ADVERT = ("About the job\n\nWe build satellite imagery pipelines in Python and PostgreSQL. "
          "You will lead a team of six engineers, set the technical direction and hire. "
          "Fully remote within Europe. We are a small, fast-growing space company.\n\n\n\n"
          "Requirements: years of Python, relational databases, leading people.\n… see more")


@pytest.mark.parametrize("url, key", [
    ("https://www.linkedin.com/jobs/view/4012345678/", "linkedin:4012345678"),
    ("https://www.linkedin.com/jobs/view/senior-em-at-orbit-4012345678?trk=x", "linkedin:4012345678"),
    ("https://www.linkedin.com/comm/jobs/view/4012345678", "linkedin:4012345678"),
    ("https://www.linkedin.com/jobs/search/?currentJobId=4012345678&keywords=python",
     "linkedin:4012345678"),
    ("https://example.com/jobs/view/4012345678", None),
    ("https://www.linkedin.com/feed/", None),
])
def test_external_id_matches_the_alert_postings(url, key):
    assert external_id_for(url) == key


def test_clean_description_drops_page_furniture():
    text = clean_description(ADVERT)
    assert text.startswith("We build satellite")
    assert text.endswith("leading people.")
    assert "\n\n\n" not in text


def test_bookmarklet_posts_to_the_page_origin():
    js = bookmarklet("http://server:8090")
    assert js.startswith("javascript:(function(){")
    assert "'http://server:8090'+'/capture'" in js
    assert "%" not in js and "\n" not in js  # javascript: URLs are percent-decoded


@pytest.mark.parametrize("a, b, same", [
    ("Sr. Backend Engineer, Python", "Senior Backend Engineer (Python)", True),
    ("Engineering Manager - Data Platform", "Engineering Manager, Data Platform", True),
    ("Senior Software Engineer", "Staff Software Engineer", False),
    ("Engineering Manager", "Engineering Manager, Payments Platform Team", False),
])
def test_title_similarity(a, b, same):
    assert (title_similarity(a, b) >= 0.75) is same


def facts(job_id, title, company="Orbit", description=None):
    job = JobFacts(job_id, title, company, [], False)
    job.descriptions = [description] if description else []
    return job


def test_jobs_without_text_borrow_it_from_one_clear_twin():
    linkedin = facts(1, "Sr. Backend Engineer, Python")
    board = facts(2, "Senior Backend Engineer (Python)", description="Python all day.")
    other_company = facts(3, "Sr. Backend Engineer, Python", company="Acme")
    assert borrow_twin_descriptions([linkedin, board, other_company]) == 1
    assert linkedin.descriptions == ["Python all day."]
    assert linkedin.twin_title == "Senior Backend Engineer (Python)"
    assert other_company.descriptions == []


def test_a_tie_between_two_twins_borrows_nothing():
    linkedin = facts(1, "Engineering Manager Data")
    one = facts(2, "Engineering Manager Data Europe", description="one")
    two = facts(3, "Engineering Manager Data Poland", description="two")
    assert borrow_twin_descriptions([linkedin, one, two]) == 0


# --- against a real database ---------------------------------------------------------------

db = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


def alert_posting(job_id="4012345678", title="Engineering Manager"):
    return RawPosting(external_id=f"linkedin:{job_id}",
                      url=f"https://www.linkedin.com/jobs/view/{job_id}/", title=title,
                      company_name="Orbit", location_text="Remote", work_mode="remote",
                      raw={"platform": "linkedin", "alert_from": "LinkedIn"})


def reasons(conn, title):  # noqa: F811
    return conn.execute("SELECT s.reasons FROM job_scores s JOIN jobs j ON j.id = s.job_id "
                        "WHERE j.title = %s", (title,)).fetchone()[0]


@db
def test_capture_is_scored_and_survives_the_next_alert_run(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    email = ensure_source(conn, "email_alerts", "Job-alert emails", "email")
    upsert_posting(conn, email, alert_posting())
    conn.commit()
    score_all(conn)
    assert "no description collected: check the advert" in reasons(conn, "Engineering Manager")

    status, page = web.capture({"url": ["https://www.linkedin.com/jobs/view/4012345678/?trk=a"],
                                "description": [ADVERT], "title": ["Engineering Manager"]})
    assert status == 200 and "Saved and rescored" in page and "Engineering Manager" in page
    assert "no description collected: check the advert" not in reasons(conn, "Engineering Manager")

    # The next alert run rewrites the posting without text; the capture still counts.
    upsert_posting(conn, email, alert_posting())
    conn.commit()
    score_all(conn)
    assert "no description collected: check the advert" not in reasons(conn, "Engineering Manager")
    assert "not in an alert yet" not in web.render_capture("http://server:8090")


@db
def test_capture_before_the_alert_is_used_when_it_arrives(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    conn.commit()  # the migrations, for web.capture's own connection
    status, page = web.capture({"url": ["https://www.linkedin.com/jobs/view/555/"],
                                "description": [ADVERT]})
    assert status == 200 and "hasn't come in a LinkedIn alert yet" in page
    assert "not in an alert yet" in web.render_capture("http://server:8090")

    email = ensure_source(conn, "email_alerts", "Job-alert emails", "email")
    upsert_posting(conn, email, alert_posting("555", "Head of Engineering"))
    conn.commit()
    score_all(conn)
    assert "no description collected: check the advert" not in reasons(conn, "Head of Engineering")


@db
def test_capture_rejects_other_pages_and_short_text(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    conn.commit()
    assert web.capture({"url": ["https://example.com/job/1"], "description": [ADVERT]})[0] == 400
    assert web.capture({"url": ["https://www.linkedin.com/jobs/view/1/"],
                        "description": ["Apply now"]})[0] == 400
    assert conn.execute("SELECT count(*) FROM page_captures").fetchone()[0] == 0


@db
def test_linkedin_job_borrows_text_from_the_company_board(conn):  # noqa: F811
    email = ensure_source(conn, "email_alerts", "Job-alert emails", "email")
    board = ensure_source(conn, "greenhouse", "Greenhouse", "ats")
    upsert_posting(conn, email, alert_posting(title="Sr. Engineering Manager, Imagery"))
    upsert_posting(conn, board, RawPosting(
        external_id="gh-1", url="https://boards.greenhouse.io/orbit/1",
        title="Senior Engineering Manager (Imagery)", company_name="Orbit", work_mode="remote",
        description=ADVERT))
    conn.commit()
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 2  # not merged
    score_all(conn)
    assert ('advert text taken from the same job listed as "Senior Engineering Manager (Imagery)"'
            in reasons(conn, "Sr. Engineering Manager, Imagery"))
