"""Reading an advert from the URL James pastes on the Add job page, without a database."""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from vacancy_finder import add_job
from vacancy_finder.collectors.ats import GreenhouseCollector, TrackedCompany

FIXTURES = Path(__file__).parent / "fixtures"
CAREERS_URL = "https://orbital.example/careers/em-data-platform?utm_source=linkedin&ref=x#apply"


class NoDb:
    """Stands in for the connection: no company is stored yet."""

    def execute(self, *args):
        return self

    def fetchone(self):
        return None


def client(pages: dict[str, httpx.Response], requests: list[str] | None = None) -> httpx.Client:
    def handler(request):
        if requests is not None:
            requests.append(str(request.url))
        return pages.get(str(request.url), httpx.Response(404))
    return httpx.Client(transport=httpx.MockTransport(handler))


def careers_page(robots: str = "User-agent: *\nDisallow: /admin/\n") -> dict[str, httpx.Response]:
    return {
        "https://orbital.example/robots.txt": httpx.Response(200, text=robots),
        "https://orbital.example/careers/em-data-platform":
            httpx.Response(200, text=(FIXTURES / "careers_jobposting.html").read_text()),
    }


def test_canonical_url_drops_tracking_and_fragment():
    assert add_job.canonical_url(CAREERS_URL) == "https://orbital.example/careers/em-data-platform"
    assert add_job.canonical_url("https://X.example/jobs?id=7&utm_medium=a") == "https://x.example/jobs?id=7"


def test_not_a_web_address():
    with pytest.raises(ValueError, match="web address"):
        add_job.read("javascript:alert(1)", NoDb(), client({}))


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/view/senior-em-at-orbit-4012345678?trk=x",
    "https://nofluffjobs.com/pl/job/senior-python-developer-acme-remote",
])
def test_linkedin_and_no_fluff_jobs_are_never_fetched(url):
    requests = []
    draft = add_job.read(url, NoDb(), client({}, requests))
    assert requests == []
    assert "never fetched" in draft.problem and not draft.description


def test_linkedin_is_keyed_like_its_alerts():
    draft = add_job.read("https://www.linkedin.com/jobs/search/?currentJobId=4012345678", NoDb(), client({}))
    assert draft.external_id == "linkedin:4012345678"
    assert draft.url == "https://www.linkedin.com/jobs/view/4012345678/"


def test_careers_page_read_from_its_job_posting_block():
    requests = []
    draft = add_job.read(CAREERS_URL, NoDb(), client(careers_page(), requests))
    assert requests[0] == "https://orbital.example/robots.txt"
    assert not draft.problem
    assert draft.external_id == draft.url == "https://orbital.example/careers/em-data-platform"
    assert draft.title == "Engineering Manager, Data Platform"
    assert draft.company == "Orbital Mapping"
    assert draft.location == "Warsaw, PL"
    assert draft.description.startswith("We turn satellite imagery")
    assert "Hybrid: Warsaw office one day a week" in draft.description
    assert draft.extra["salary_min"] == Decimal("30000")
    assert draft.extra["salary_period"] == "month" and draft.extra["salary_currency"] == "PLN"
    assert draft.extra["closes_kind"] == "expiry"
    assert draft.read_from == "orbital.example"


def test_extra_fields_survive_the_form():
    draft = add_job.read(CAREERS_URL, NoDb(), client(careers_page()))
    assert add_job.extra_from_json(add_job.extra_to_json(draft.extra)) == draft.extra
    # Anything else in the hidden field is dropped.
    assert add_job.extra_from_json('{"salary_min": "x", "title": "y", "posted_at": "soon",'
                                   ' "closes_kind": "expiry"}') == {}
    assert add_job.extra_from_json("not json") == {}


def test_robots_txt_that_bars_the_page_is_respected():
    requests = []
    draft = add_job.read(CAREERS_URL, NoDb(), client(careers_page("User-agent: *\nDisallow: /careers/\n"),
                                                    requests))
    assert requests == ["https://orbital.example/robots.txt"]
    assert "robots.txt" in draft.problem and not draft.title


def test_page_without_job_posting_goes_to_the_paste_box():
    pages = {"https://plain.example/job/1": httpx.Response(200, text="<html><p>We are hiring!</p></html>")}
    draft = add_job.read("https://plain.example/job/1", NoDb(), client(pages))
    assert "JobPosting" in draft.problem and not draft.description


def test_blocked_page_goes_to_the_paste_box():
    pages = {"https://plain.example/job/1": httpx.Response(403, text="Just a moment...")}
    draft = add_job.read("https://plain.example/job/1", NoDb(), client(pages))
    assert "answered 403" in draft.problem


def test_careers_system_advert_picked_from_the_company_feed():
    feed = (FIXTURES / "greenhouse.json").read_text()
    requests = []
    pages = {"https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true": httpx.Response(200, text=feed)}
    draft = add_job.read("https://job-boards.greenhouse.io/acme/jobs/4012345?gh_src=abc", NoDb(),
                         client(pages, requests))
    assert requests == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"]
    assert draft.ats == ("greenhouse", "acme")
    assert draft.title == "Staff Software Engineer, Ground Segment"
    assert draft.company == "Acme"  # a guess from the slug, for James to correct
    assert draft.read_from == "Greenhouse job boards"
    assert draft.work_mode == "hybrid"


def test_careers_system_advert_no_longer_listed():
    feed = json.dumps({"jobs": []})
    pages = {"https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true": httpx.Response(200, text=feed)}
    draft = add_job.read("https://job-boards.greenhouse.io/acme/jobs/999", NoDb(), client(pages))
    assert draft.ats == ("greenhouse", "acme") and "doesn't list this advert" in draft.problem


def test_pick_advert_by_id_in_the_address():
    postings = list(GreenhouseCollector().parse(json.loads((FIXTURES / "greenhouse.json").read_text()),
                                                TrackedCompany(1, "Acme", "acme")))
    picked = add_job.pick_advert(postings, "https://boards.greenhouse.io/embed/job_app?for=acme&token=4012345")
    assert picked is not None and picked.external_id == "acme:4012345"
    assert add_job.pick_advert(postings, "https://job-boards.greenhouse.io/acme/jobs/1") is None


def test_placeholder_names():
    assert add_job.placeholder_name("acme-space") == "Acme Space"
    assert add_job.placeholder_name("orbital.jobs.personio.de") == "Orbital"
    assert add_job.placeholder_name("ledgebrook/D9.006") == "Ledgebrook"


def test_job_posting_found_alone_or_in_a_list():
    item = {"@type": "JobPosting", "title": "Staff Engineer"}
    for block in (item, [{"@type": "WebSite"}, item]):
        page = f'<script type="application/ld+json">{json.dumps(block)}</script>'
        assert add_job.find_job_posting(page)["title"] == "Staff Engineer"
    remote = add_job.posting_from_ld({**item, "jobLocationType": "TELECOMMUTE",
                                      "datePosted": "not a date"}, "k", "u")
    assert remote.work_mode == "remote" and remote.location_text == "Remote" and remote.posted_at is None
    assert isinstance(add_job.posting_from_ld({**item, "datePosted": "2026-11-02"}, "k", "u").posted_at,
                      datetime)


ASHBY_FEED = "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
EMBED_URL = "https://www.acme.example/careers?ashby_location_id=x1&ashby_jid=2a3b"


def embedding_page(embed: str = '<script src="https://jobs.ashbyhq.com/acme/embed?version=2">'
                   ) -> dict[str, httpx.Response]:
    return {
        "https://www.acme.example/robots.txt": httpx.Response(200, text="User-agent: *\nAllow: /\n"),
        EMBED_URL: httpx.Response(200, text=f"<html><body><div id='ashby_embed'></div>{embed}</body></html>"),
        ASHBY_FEED: httpx.Response(200, text=(FIXTURES / "ashby.json").read_text()),
    }


def test_company_page_embedding_its_ashby_board():
    requests = []
    draft = add_job.read(EMBED_URL, NoDb(), client(embedding_page(), requests))
    assert requests == ["https://www.acme.example/robots.txt", EMBED_URL, ASHBY_FEED]
    assert draft.ats == ("ashby", "acme")
    assert draft.title == "Engineering Lead"  # the advert ashby_jid names, not the other one
    assert draft.feed_external_id == "acme:2a3b"
    assert draft.read_from == "Ashby job boards"
    assert draft.url == EMBED_URL  # the address James pasted is kept


def test_embedded_board_needs_the_advert_param_and_one_board():
    page = '<a href="https://jobs.ashbyhq.com/acme">Jobs</a>'
    assert add_job.embedded_board(EMBED_URL, page) == ("ashby", "acme")
    assert add_job.embedded_board("https://www.acme.example/careers", page) is None
    two = page + '<a href="https://jobs.ashbyhq.com/other">'
    assert add_job.embedded_board(EMBED_URL, two) is None
    assert add_job.embedded_board("https://acme.example/jobs?gh_jid=4012345",
                                  '<script src="https://boards.greenhouse.io/embed/job_board?for=acme">'
                                  ) == ("greenhouse", "acme")


def test_company_page_without_a_board_is_read_as_any_page():
    draft = add_job.read(EMBED_URL, NoDb(), client(embedding_page(embed="")))
    assert draft.ats is None and "JobPosting" in draft.problem
