"""Himalayas looking up adverts its role searches no longer return, against a real database."""

import httpx
import pytest

from vacancy_finder.collectors.remote import HimalayasCollector
from vacancy_finder.runner import run_collector

from .test_remote import HIMALAYAS_JOB
from .test_store import DB_URL, conn, count  # noqa: F401  (conn is a fixture)

pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

GRUNS = "https://himalayas.app/companies/gruns/jobs/"
EXPIRED = GRUNS + "director-of-data-engineering"
LIVE = GRUNS + "senior-creative-strategist-international"
OTHER = "https://himalayas.app/companies/acme/jobs/staff-engineer"


def job(guid):
    company = "Grüns" if guid.startswith(GRUNS) else "Acme"
    return dict(HIMALAYAS_JOB, guid=guid, companyName=company, title=guid.rsplit("/", 1)[1])


class Site:
    """The role search ("python") returns `listed`; a company search returns that company's
    adverts among `alive`, unless the company is in `failing`."""

    def __init__(self, listed, alive=(), failing=()):
        self.listed, self.alive, self.failing = listed, alive, failing
        self.lookups = []

    def handler(self, request):
        q = request.url.params["q"]
        if q == "python":
            return httpx.Response(200, json={"jobs": [job(g) for g in self.listed]})
        self.lookups.append(q)
        if q in self.failing:
            return httpx.Response(503)
        jobs = [job(g) for g in self.alive if job(g)["companyName"] == q]
        other = dict(job("https://himalayas.app/companies/x/jobs/y"), companyName="Someone else")
        return httpx.Response(200, json={"jobs": jobs + [other]})

    def collector(self, **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return HimalayasCollector(client, delay_seconds=0, queries=("python",), **kwargs)


def open_urls(conn):
    rows = conn.execute("SELECT url FROM postings WHERE closed_at IS NULL ORDER BY url").fetchall()
    return [r[0] for r in rows]


def test_expired_advert_is_closed_on_the_next_run(conn):  # noqa: F811
    run_collector(conn, Site([EXPIRED, LIVE]).collector())
    # Both drop out of the role search; only LIVE is still in Grüns's results.
    site = Site([], alive=[LIVE])
    run_collector(conn, site.collector())
    assert site.lookups == ["Grüns"]  # one search per company, not per advert
    assert open_urls(conn) == [LIVE]
    assert count(conn, "SELECT count(*) FROM jobs WHERE status = 'closed'") == 1


def test_advert_still_listed_counts_as_seen(conn):  # noqa: F811
    run_collector(conn, Site([LIVE]).collector())
    conn.execute("UPDATE postings SET last_seen_at = now() - interval '30 days'")
    conn.commit()
    run_collector(conn, Site([], alive=[LIVE]).collector())
    # Still in its company's results, so not closed as stale though the role search lost it.
    assert open_urls(conn) == [LIVE]


def test_failed_or_empty_lookup_decides_nothing(conn):  # noqa: F811
    run_collector(conn, Site([EXPIRED, OTHER]).collector())
    # Grüns's search fails; Acme's finds no Acme advert at all, so the name may not match.
    run_collector(conn, Site([], failing=["Grüns"]).collector())
    assert open_urls(conn) == [OTHER, EXPIRED]


def test_tracked_companies_go_first_within_the_limit(conn):  # noqa: F811
    run_collector(conn, Site([OTHER, EXPIRED]).collector())
    conn.execute("UPDATE postings SET last_seen_at = now() - interval '1 day' WHERE url = %s", (OTHER,))
    conn.execute(
        "INSERT INTO job_reviews (job_id, decision) SELECT job_id, 'interested' FROM postings WHERE url = %s",
        (EXPIRED,),
    )
    conn.commit()
    site = Site([], alive=[OTHER, LIVE])
    run_collector(conn, site.collector(max_checks=1))
    assert site.lookups == ["Grüns"]
    # The interested job's only advert has gone, so it moves to Missed.
    assert conn.execute("SELECT decision FROM job_reviews").fetchone()[0] == "missed"


def test_listed_adverts_are_not_looked_up(conn):  # noqa: F811
    run_collector(conn, Site([LIVE]).collector())
    site = Site([LIVE])
    run_collector(conn, site.collector())
    assert site.lookups == []


def test_missing_advert_past_its_expiry_date_is_closed(conn):  # noqa: F811
    run_collector(conn, Site([LIVE, OTHER]).collector())
    conn.execute("UPDATE postings SET closes_at = now() - interval '1 hour', closes_kind = 'expiry'")
    conn.commit()
    # OTHER is still in the role search, so its stale expiry date doesn't close it; LIVE
    # isn't, and nothing is looked up for it (max_checks=0), but its expiry has passed.
    run_collector(conn, Site([OTHER]).collector(max_checks=0))
    assert open_urls(conn) == [OTHER]
