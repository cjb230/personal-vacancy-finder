"""Finding the employer's careers page for Interested and Maybe Himalayas jobs, against a real
database, with the careers sites faked."""

from pathlib import Path

import httpx
import pytest

from vacancy_finder import web
from vacancy_finder.find_employer_adverts import run
from vacancy_finder.matching import score_all
from vacancy_finder.models import RawPosting
from vacancy_finder.store import ensure_source, upsert_posting

from .test_store import DB_URL, conn, count  # noqa: F401  (conn is a fixture)

pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

FIXTURES = Path(__file__).parent / "fixtures"
COMEET = "https://www.comeet.com/jobs/ledgebrook/D9.006/staff-engineer/CA.17C"


def himalayas(conn, company, title, description="Python and PostgreSQL. Lead the team."):  # noqa: F811
    source = ensure_source(conn, "himalayas", "Himalayas", "board")
    slug = company.lower().replace(" ", "-")
    guid = f"https://himalayas.app/companies/{slug}/jobs/{title.lower().replace(' ', '-')}"
    upsert_posting(conn, source, RawPosting(
        external_id=guid, url=guid, title=title, company_name=company, location_text="Poland",
        work_mode="remote", description=description, raw={"companyName": company}))
    return conn.execute("SELECT job_id FROM postings WHERE external_id = %s", (guid,)).fetchone()[0]


class Sites:
    """Ledgebrook's Comeet page, and a Greenhouse board for "acmespace" and for "orbit" (both
    the fixture's two adverts); every other careers address is a 404."""

    def __init__(self):
        self.requests = []

    def handler(self, request):
        url = str(request.url)
        self.requests.append(url)
        if url == "https://www.comeet.com/jobs/ledgebrook/D9.006":
            return httpx.Response(200, text=(FIXTURES / "comeet.html").read_text())
        if url.startswith(("https://boards-api.greenhouse.io/v1/boards/acmespace/jobs",
                           "https://boards-api.greenhouse.io/v1/boards/orbit/jobs")):
            return httpx.Response(200, text=(FIXTURES / "greenhouse.json").read_text())
        return httpx.Response(404)


def setup_jobs(conn):  # noqa: F811
    staff = himalayas(conn, "Ledgebrook", "Staff Engineer",
                      f"Python and PostgreSQL. Lead the team. Apply: {COMEET}?utm_source=himalayas.app")
    ground = himalayas(conn, "Acme Space", "Staff Software Engineer, Ground Segment")
    platform = himalayas(conn, "Orbit", "Head of Platform")
    ignored = himalayas(conn, "Quiet Co", "Staff Engineer")
    conn.commit()
    score_all(conn)
    for job in (staff, platform):
        web.save_review(job, "interested")
    conn.execute("UPDATE job_scores SET verdict = 'maybe' WHERE job_id = %s", (ground,))
    conn.execute("UPDATE job_scores SET verdict = 'match' WHERE job_id = %s", (ignored,))
    conn.commit()
    return staff, ground, platform


def test_dry_run_reports_without_changing_anything(conn, monkeypatch):  # noqa: F811
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    setup_jobs(conn)
    out = []
    with httpx.Client(transport=httpx.MockTransport(Sites().handler)) as http:
        run(conn, http, dry_run=True, delay=0, say=out.append)
    text = "\n".join(out)
    assert "3 Himalayas jobs at 3 companies" in text  # the Match job isn't one
    assert "Ledgebrook: comeet ledgebrook/D9.006, found from a link in an advert" in text
    assert "(would follow it)" in text and "Nothing was changed" in text
    assert count(conn, "SELECT count(*) FROM companies WHERE tracked") == 0
    assert count(conn, "SELECT count(*) FROM postings") == 4


def test_finds_follows_and_rescores(conn, monkeypatch):  # noqa: F811
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    staff, ground, platform = setup_jobs(conn)
    sites = Sites()
    out = []
    with httpx.Client(transport=httpx.MockTransport(sites.handler)) as http:
        companies = run(conn, http, delay=0, say=out.append)
    text = "\n".join(out)
    found = {c.name: c.found for c in companies}
    assert found == {
        "Acme Space": ("greenhouse", "acmespace", "its name"),
        "Ledgebrook": ("comeet", "ledgebrook/D9.006", "a link in an advert"),
        "Orbit": None,  # its name has a board, but not with this job: not taken
    }
    assert conn.execute("SELECT name, ats_kind, ats_slug FROM companies WHERE tracked "
                        "ORDER BY name").fetchall() == [
        ("Acme Space", "greenhouse", "acmespace"), ("Ledgebrook", "comeet", "ledgebrook/D9.006")]

    verdict, reasons = conn.execute("SELECT verdict, reasons FROM job_scores WHERE job_id = %s",
                                    (staff,)).fetchone()
    assert verdict == "no" and "checked against the employer's advert" in reasons
    assert "Staff Engineer: interested" not in text  # reported by verdict, not decision
    assert "Staff Engineer: maybe" in text or "Staff Engineer: match" in text
    assert "-> no " in text and "only for the US/North America" in text
    assert "checked against the employer's advert" in conn.execute(
        "SELECT reasons FROM job_scores WHERE job_id = %s", (ground,)).fetchone()[0]

    assert "Orbit: no careers page found. Rejected: greenhouse orbit (2 adverts" in text
    assert ("Head of Platform" in text and "use Employer link on "
            "https://himalayas.app/companies/orbit/jobs/head-of-platform" in text)
    assert "careers page followed for 2 of 3 companies" in text
    # Each name is tried once per careers system, and no Himalayas page is fetched.
    assert not any("himalayas.app" in url for url in sites.requests)
    assert len(sites.requests) == len(set(sites.requests))


def test_skip_and_aggregator_options(conn, monkeypatch):  # noqa: F811
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    staff, ground, _ = setup_jobs(conn)
    # setup_jobs sets this job's verdict to maybe by hand; rescoring restores the real one,
    # so compare the score, which only the employer's advert could change.
    before = conn.execute("SELECT score FROM job_scores WHERE job_id = %s", (ground,)).fetchone()[0]
    out = []
    with httpx.Client(transport=httpx.MockTransport(Sites().handler)) as http:
        companies = run(conn, http, delay=0, say=out.append,
                        skip=["ledgebrook"], aggregators=["ACME SPACE"])  # any case
    text = "\n".join(out)
    assert {c.name for c in companies} == {"Acme Space", "Orbit"}  # Ledgebrook left alone
    assert "Skipped: Ledgebrook" in text
    assert conn.execute("SELECT tracked FROM companies WHERE name = 'Ledgebrook'").fetchone() == (
        False,)
    assert conn.execute("SELECT tracked, aggregator FROM companies WHERE name = 'Acme Space'"
                        ).fetchone() == (True, True)
    assert "now followed as an aggregator" in text
    # An aggregator's copy is just another board's: it overrides nothing.
    score, reasons = conn.execute(
        "SELECT score, reasons FROM job_scores WHERE job_id = %s", (ground,)).fetchone()
    assert score == before
    assert not any("employer" in r for r in reasons)
