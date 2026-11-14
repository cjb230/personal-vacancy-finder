"""Saving a job added by URL on the Add job page, against a real database."""

import os
from pathlib import Path

import httpx
import pytest

from vacancy_finder import add_job, web
from vacancy_finder.matching import score_all
from vacancy_finder.models import RawPosting
from vacancy_finder.store import ensure_source, upsert_posting

from .test_add_job import CAREERS_URL, careers_page, client
from .test_store import DB_URL, conn, count  # noqa: F401  (conn is a fixture)

pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

FIXTURES = Path(__file__).parent / "fixtures"
GREENHOUSE_URL = "https://job-boards.greenhouse.io/acme/jobs/4012345"
GREENHOUSE_FEED = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
TEXT = ("We build satellite imagery pipelines in Python and PostgreSQL. You will lead a team of six "
        "engineers, set the technical direction and hire. Fully remote within Europe. We are a small, "
        "fast-growing space company with a ground segment team in Warsaw.")


def greenhouse(requests=None):
    return client({GREENHOUSE_FEED: httpx.Response(200, text=(FIXTURES / "greenhouse.json").read_text())},
                  requests)


def form(draft: add_job.Draft, **changes) -> dict[str, list[str]]:
    fields = {"url": draft.url, "external_id": draft.external_id, "title": draft.title,
              "company": draft.company, "location": draft.location, "work_mode": draft.work_mode,
              "description": draft.description, "read_from": draft.read_from,
              "extra": add_job.extra_to_json(draft.extra)}
    if draft.ats:
        fields |= {"ats_kind": draft.ats[0], "ats_slug": draft.ats[1]}
    return {k: [v] for k, v in (fields | changes).items()}


def test_careers_page_advert_joins_the_board_copy(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    conn.commit()  # the migrations, for the page's own connections
    board = ensure_source(conn, "himalayas", "Himalayas", "board")
    upsert_posting(conn, board, RawPosting(
        external_id="h1", url="https://himalayas.app/x", title="Engineering Manager - Data Platform",
        company_name="Orbital Mapping S.A.", work_mode="remote"))
    conn.commit()
    score_all(conn)

    draft = add_job.read(CAREERS_URL, conn, client(careers_page()))
    monkeypatch.setattr(web, "add_client", lambda: client(careers_page()))
    status, html = web.read_added({"url": [CAREERS_URL]})
    assert status == 200 and "Engineering Manager, Data Platform" in html
    assert "Also follow" not in html  # not a careers system we read

    status, html = web.save_added(form(draft))
    assert status == 200 and "It joined a job already found elsewhere." in html
    assert count(conn, "SELECT count(*) FROM jobs") == 1
    job_id, salary = conn.execute(
        "SELECT p.job_id, p.salary_max FROM postings p JOIN sources s ON s.id = p.source_id"
        " WHERE s.key = 'manual'").fetchone()
    assert salary == 38000
    assert conn.execute("SELECT kind FROM sources WHERE key = 'manual'").fetchone() == ("manual",)
    assert count(conn, f"SELECT count(*) FROM job_scores WHERE job_id = {job_id}") == 1
    assert "Engineering Manager, Data Platform" in web.render_add()
    # Jobs added by hand neither confirm an advert nor show in the last-checked box.
    assert conn.execute("SELECT still_listed FROM job_confirmation WHERE job_id = %s",
                        (job_id,)).fetchone() == (True,)  # still listed thanks to Himalayas
    assert "Added by hand" not in [name for name, *_ in conn.execute(web.LAST_CHECKED, {"tz": "UTC"})]
    assert "Added by hand, Himalayas" in web.render("match", "", 0)  # the job's sources

    # Saving again replaces the same posting instead of adding another.
    web.save_added(form(draft, location="Remote, Europe"))
    assert count(conn, "SELECT count(*) FROM postings") == 2


def test_pasted_advert_makes_a_new_job(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    conn.commit()  # the migrations, for the page's own connections
    draft = add_job.Draft(url="https://plain.example/job/1", external_id="https://plain.example/job/1",
                          title="Staff Python Engineer", company="Orbit", description=TEXT,
                          work_mode="remote")
    status, html = web.save_added(form(draft))
    assert status == 200 and "It is a new job." in html
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    assert count(conn, f"SELECT count(*) FROM job_confirmation WHERE job_id = {job_id}") == 0

    status, html = web.save_added(form(draft, description="Too short"))
    assert status == 400 and "too short" in html and 'value="Staff Python Engineer"' in html
    status, html = web.save_added(form(draft, company="(unknown)"))
    assert status == 400 and "company" in html
    status, html = web.save_added(form(draft, url="ftp://x"))
    assert status == 400


def test_linkedin_advert_also_counts_as_a_capture(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    conn.commit()  # the migrations, for the page's own connections
    draft = add_job.read("https://www.linkedin.com/jobs/view/4012345678/", conn, client({}))
    web.save_added(form(draft, title="Engineering Manager", company="Orbit", description=TEXT))
    assert conn.execute("SELECT external_id FROM postings").fetchone() == ("linkedin:4012345678",)
    assert conn.execute("SELECT external_id, company FROM page_captures").fetchone() == (
        "linkedin:4012345678", "Orbit")


def test_careers_system_company_followed(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    conn.commit()  # the migrations, for the page's own connections
    draft = add_job.read(GREENHOUSE_URL, conn, greenhouse())
    assert "too short" in draft.problem  # the fixture's description is brief
    assert "Also follow" in web.render_add_form(draft)
    monkeypatch.setattr(add_job.employer, "client", greenhouse)

    status, html = web.save_added(form(draft, company="Acme Space", description=TEXT, follow="1"))
    assert status == 200 and "Acme Space is now followed on Greenhouse job boards: 2 adverts" in html
    assert conn.execute("SELECT ats_kind, ats_slug, tracked FROM companies").fetchone() == (
        "greenhouse", "acme", True)
    # The employer's own copy joined the job added by hand.
    job_id = conn.execute("SELECT job_id FROM postings WHERE external_id = %s",
                          (draft.external_id,)).fetchone()[0]
    assert count(conn, f"SELECT count(*) FROM postings WHERE job_id = {job_id}") == 2

    # Read again, the stored company's name is used, and it is already followed.
    again = add_job.read(GREENHOUSE_URL, conn, greenhouse())
    assert again.company == "Acme Space"
    status, html = web.save_added(form(again, description=TEXT, follow="1"))
    assert "already followed" in html
