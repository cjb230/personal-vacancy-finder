"""Space-Careers: sitemap, advert pages and which adverts get re-fetched."""

from datetime import UTC, datetime
from pathlib import Path

import httpx

from vacancy_finder.collectors import spacecareers
from vacancy_finder.collectors.justjoinit import SitemapEntry
from vacancy_finder.collectors.spacecareers import (
    SpaceCareersCollector,
    parse_job_page,
    parse_sitemap,
)

FIXTURES = Path(__file__).parent / "fixtures"
ENTRY = SitemapEntry(
    "https://www.space-careers.com/job_display/298473/Chief_Engineer_Acme_Space_Bordeaux_or_Munich_Multiple_Countries",
    "2026-10-30",
)


def test_sitemap_keeps_only_adverts():
    """The site's sitemap uses an https:// namespace, not the standard http:// one."""
    entries = parse_sitemap((FIXTURES / "spacecareers_sitemap.xml").read_bytes())
    assert entries == [
        ENTRY,
        SitemapEntry("https://www.space-careers.com/job_display/298412/Ground_Systems_Engineer_Beta_Orbital_Helsinki_Finland",
                     "2026-10-29"),
    ]


def test_job_page():
    posting = parse_job_page((FIXTURES / "spacecareers_job.html").read_text(), ENTRY)
    assert posting.external_id == "298473"
    assert posting.url == ENTRY.url
    assert posting.title == "Chief Engineer - High Thrust Engine Expert"
    assert posting.company_name == "Acme Space"
    assert posting.location_text == "Bordeaux or Munich, Multiple Countries"
    assert posting.work_mode == "onsite"  # from More Job Searches, not the "Remote (12)" menu
    assert posting.posted_at == datetime(2026, 10, 30, tzinfo=UTC)
    assert "Define the technical roadmap" in posting.description
    assert "mention Space-Careers" not in posting.description
    assert posting.language == "en"


def test_page_without_title_is_skipped():
    assert parse_job_page("<html><body>Not found</body></html>", ENTRY) is None


def test_only_new_or_changed_adverts_fetched(monkeypatch):
    sitemap = (FIXTURES / "spacecareers_sitemap.xml").read_bytes()
    page = (FIXTURES / "spacecareers_job.html").read_text()
    requested = []

    def handler(request):
        requested.append(str(request.url))
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, content=sitemap)
        return httpx.Response(200, text=page)

    touched = []
    monkeypatch.setattr(spacecareers, "known_lastmods", lambda conn, key: {"298412": "2026-10-29"})
    monkeypatch.setattr(spacecareers, "touch_postings", lambda conn, key, ids: touched.extend(ids))
    collector = SpaceCareersCollector(httpx.Client(transport=httpx.MockTransport(handler)),
                                      delay_seconds=0)
    postings = list(collector.collect(conn=None))
    assert [p.external_id for p in postings] == ["298473"]
    assert touched == ["298412"]
    assert requested == ["https://www.space-careers.com/sitemap.xml", ENTRY.url]
