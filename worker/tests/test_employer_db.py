"""The employer's own advert outranking a board's copy, and the Employer link button that finds
it, against a real database."""

import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from vacancy_finder import web
from vacancy_finder.matching import score_all
from vacancy_finder.models import RawPosting
from vacancy_finder.store import ensure_source, upsert_posting

from .test_store import DB_URL, conn, count  # noqa: F401  (conn is a fixture)

pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

FIXTURES = Path(__file__).parent / "fixtures"
APPLY_LINK = ("https://www.comeet.com/jobs/ledgebrook/D9.006/staff-engineer/CA.17C"
              "?utm_source=himalayas.app&utm_medium=himalayas.app&ref=himalayas.app")


def himalayas(conn, title, location):  # noqa: F811
    source = ensure_source(conn, "himalayas", "Himalayas", "board")
    guid = f"https://himalayas.app/companies/ledgebrook/jobs/{title.lower().replace(' ', '-')}"
    upsert_posting(conn, source, RawPosting(
        external_id=guid, url=guid, title=title, company_name="Ledgebrook", location_text=location,
        work_mode="remote", description="Python and PostgreSQL. You will lead the platform team.",
        raw={"companyName": "Ledgebrook"}))


def job_id(conn, title):  # noqa: F811
    return conn.execute("SELECT id FROM jobs WHERE title = %s", (title,)).fetchone()[0]


def score(conn, job):  # noqa: F811
    return conn.execute("SELECT verdict, reasons FROM job_scores WHERE job_id = %s",
                        (job,)).fetchone()


def comeet(requests, status=200):
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(status, text=(FIXTURES / "comeet.html").read_text())
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_employer_link_tracks_the_company_and_its_advert_wins(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    himalayas(conn, "Staff Engineer", "Poland")
    himalayas(conn, "Full Stack Engineer", "Canada; Poland; United States")
    conn.commit()
    score_all(conn)
    staff, full_stack = job_id(conn, "Staff Engineer"), job_id(conn, "Full Stack Engineer")
    assert score(conn, staff)[0] != "no"  # Himalayas alone says Poland is fine

    # An interested Himalayas job at a company whose careers page isn't known offers the button.
    web.save_review(staff, "interested")
    page = web.render("interested", "", 0)
    assert f'id="employer-{staff}"' in page and "Employer link" in page

    with pytest.raises(ValueError, match="careers"):
        web.save_employer_link(staff, "https://example.com/jobs/1", client=comeet([]))

    requests = []
    name, found = web.save_employer_link(staff, APPLY_LINK, client=comeet(requests))
    assert (name, found) == ("Ledgebrook", 2)
    assert requests == ["https://www.comeet.com/jobs/ledgebrook/D9.006"]  # the company's page
    assert conn.execute("SELECT ats_kind, ats_slug, tracked FROM companies").fetchone() == (
        "comeet", "ledgebrook/D9.006", True)
    # The employer's adverts joined the Himalayas jobs of the same title.
    assert count(conn, "SELECT count(*) FROM postings WHERE job_id = %s" % staff) == 2

    verdict, reasons = score(conn, staff)
    assert verdict == "no" and any("only for the US/North America" in r for r in reasons)
    assert "checked against the employer's advert" in reasons
    _, reasons = score(conn, full_stack)  # Comeet says "Remote - Poland": the location holds
    assert "remote, Europe/Poland OK (+15)" in reasons
    assert not any("North America" in r for r in reasons)
    assert "checked against the employer's advert" in reasons

    # Known now, so no more button.
    assert f'id="employer-{staff}"' not in web.render("interested", "", 0)


def test_employer_link_that_cannot_be_read_now_still_tracks(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    himalayas(conn, "Staff Engineer", "Poland")
    conn.commit()
    score_all(conn)
    staff = job_id(conn, "Staff Engineer")
    with pytest.raises(web.EmployerLinkError, match="next"):
        web.save_employer_link(staff, APPLY_LINK, client=comeet([], status=503))
    assert conn.execute("SELECT ats_kind, tracked FROM companies").fetchone() == ("comeet", True)


def test_employer_link_route(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    himalayas(conn, "Staff Engineer", "Poland")
    conn.commit()
    score_all(conn)
    staff = job_id(conn, "Staff Engineer")
    monkeypatch.setattr(web, "employer_client", lambda: comeet([]))

    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with httpx.Client() as client:
            ok = client.post(f"{base}/employer-link", data={
                "job_id": staff, "url": APPLY_LINK, "back": f"/?view=maybe#job-{staff}"})
            bad = client.post(f"{base}/employer-link", data={"job_id": staff, "url": "nonsense"})
    finally:
        server.shutdown()
    assert ok.status_code == 303 and ok.headers["location"] == f"/?view=maybe#job-{staff}"
    assert bad.status_code == 400 and "careers" in bad.text
    assert score(conn, staff)[0] == "no"
