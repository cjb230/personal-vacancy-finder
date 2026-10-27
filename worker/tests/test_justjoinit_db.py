"""Just Join IT collector against a real database, with HTTP answered by a stub."""

from pathlib import Path

import httpx
import pytest

from vacancy_finder.collectors.justjoinit import JustJoinItCollector
from vacancy_finder.runner import run_collector

from .test_store import DB_URL, conn, count  # noqa: F401  (conn is a fixture)

pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

FIXTURES = Path(__file__).parent / "fixtures"
SLUG = "shelf-senior-backend-python-engineer-warszawa-python-a3b545e2"
INDEX = (FIXTURES / "justjoinit_sitemap_index.xml").read_bytes()
PART = (FIXTURES / "justjoinit_sitemap_part0.xml").read_text()
PAGE = (FIXTURES / "justjoinit_offer.html").read_text()


class Site:
    """Serves the sitemap and one advert page, and records which pages were fetched."""

    def __init__(self, part=PART):
        self.part = part
        self.pages_fetched = []

    def handler(self, request):
        path = request.url.path
        if path == "/sitemaps/active-jobs.xml":
            return httpx.Response(200, content=INDEX)
        if path == "/sitemaps/active-jobs/part0.xml":
            return httpx.Response(200, content=self.part.encode())
        self.pages_fetched.append(path)
        if path == f"/job-offer/{SLUG}":
            return httpx.Response(200, text=PAGE)
        return httpx.Response(404)

    def collector(self):
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return JustJoinItCollector(client, delay_seconds=0)


def test_only_new_or_changed_adverts_are_fetched(conn):  # noqa: F811
    site = Site()
    run_collector(conn, site.collector())
    # The accountant advert is filtered out by keyword; the manager one 404s.
    assert site.pages_fetched == [
        f"/job-offer/{SLUG}", "/job-offer/beta-engineering-manager-krakow-java-87654321"
    ]
    assert count(conn, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 1

    site.pages_fetched.clear()
    run_collector(conn, site.collector())
    assert site.pages_fetched == ["/job-offer/beta-engineering-manager-krakow-java-87654321"]
    assert count(conn, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 1

    changed = Site(PART.replace("<lastmod>2026-10-25</lastmod>", "<lastmod>2026-10-27</lastmod>", 1))
    run_collector(conn, changed.collector())
    assert f"/job-offer/{SLUG}" in changed.pages_fetched


def test_advert_gone_from_sitemap_is_closed(conn):  # noqa: F811
    run_collector(conn, Site().collector())
    run_collector(conn, Site(PART.replace(SLUG, "someone-else-entirely-00000000")).collector())
    assert count(conn, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 0


def test_cap_defers_the_rest_without_closing(conn):  # noqa: F811
    run_collector(conn, Site().collector())
    capped = Site(PART.replace("<lastmod>2026-10-25</lastmod>", "<lastmod>2026-10-27</lastmod>", 1))
    collector = capped.collector()
    collector.max_pages = 1
    run_collector(conn, collector)
    assert len(capped.pages_fetched) == 1
    assert count(conn, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 1
