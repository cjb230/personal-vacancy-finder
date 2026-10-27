"""Parsing tests for the remote-board collectors. Fixture records follow the real
responses observed on 2026-10-27, with long descriptions shortened."""

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from vacancy_finder.collectors.remote import (
    ArbeitnowCollector,
    HackerNewsHiringCollector,
    HimalayasCollector,
    RemoteOkCollector,
    WeWorkRemotelyCollector,
    fix_mojibake,
    hn_salary,
    split_hn_first_line,
)

HIMALAYAS_JOB = {
    "title": "Support Analyst", "companyName": "Cayenta", "employmentType": "Full Time",
    "minSalary": 60000, "maxSalary": 75000, "salaryPeriod": "annual", "seniority": ["Mid-level"],
    "currency": "USD", "locationRestrictions": ["Canada"], "timezoneRestrictions": [-8, -7],
    "description": '<p><a href="https://himalayas.app/companies/cayenta">Cayenta</a>, a division of Harris.</p>',
    "pubDate": 1793096989,
    "applicationLink": "https://himalayas.app/companies/cayenta/jobs/support-analyst-4409536097?utm_source=api",
    "guid": "https://himalayas.app/companies/cayenta/jobs/support-analyst-4409536097",
}

REMOTEOK = [
    {"last_updated": 1793001606, "legal": "Please link back ..."},
    {"slug": "remote-senior-software-engineer-latam-wagmo-1137572", "id": "1137572",
     "date": "2026-10-26T08:00:06+00:00", "company": "Wagmo", "position": "Senior Software Engineer LATAM",
     "tags": ["dev", "python"], "description": "<p>Remote role.</p><div>Â </div>",
     "location": "", "salary_min": 100000, "salary_max": 140000,
     "url": "https://remoteOK.com/remote-jobs/remote-senior-software-engineer-latam-wagmo-1137572"},
    {"slug": "x", "id": "2", "date": "2026-10-26T08:00:06+00:00", "company": "NoPay",
     "position": "Engineer", "location": "Europe", "salary_min": 0, "salary_max": 0},
]

WWR_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>We Work Remotely</title>
<item>
  <title>PagerDuty: Senior Director, Field Enablement</title>
  <region>Anywhere in the World</region>
  <category>Full-Stack Programming</category>
  <description>&lt;p&gt;&lt;strong&gt;Headquarters:&lt;/strong&gt; Remote&lt;/p&gt;</description>
  <pubDate>Tue, 06 Oct 2026 07:31:16 +0000</pubDate>
  <guid>https://weworkremotely.com/remote-jobs/pagerduty-senior-director-field-enablement</guid>
  <link>https://weworkremotely.com/remote-jobs/pagerduty-senior-director-field-enablement</link>
</item>
</channel></rss>"""

ARBEITNOW = {
    "data": [
        {"slug": "golang-backend-engineer-london-14945", "company_name": "Deliveroo ",
         "title": "Golang Backend Engineer", "description": "<p>Our Global Structure</p>",
         "remote": False, "url": "https://www.arbeitnow.co.uk/jobs/companies/deliveroo/x",
         "tags": [], "job_types": [], "location": "London, England, United Kingdom", "created_at": 1793054726},
        {"slug": "staff-engineer-remote-1", "company_name": "Acme", "title": "Staff Engineer",
         "description": "<p>Remote</p>", "remote": True, "url": "https://www.arbeitnow.com/jobs/acme/1",
         "tags": [], "job_types": ["full time"], "location": "Berlin", "created_at": 1793054726},
        {"slug": "python-dev-warsaw-2", "company_name": "Beta", "title": "Python Developer",
         "description": "", "remote": False, "url": "https://www.arbeitnow.com/jobs/beta/2",
         "tags": [], "job_types": [], "location": "Warsaw, Poland", "created_at": 1793054726},
    ],
    "links": {"next": None},
}

HN_HITS = {"hits": [
    {"objectID": "49922570", "title": "Ask HN: Who wants to be hired? (October 2026)"},
    {"objectID": "49922569", "title": "Ask HN: Who is hiring? (October 2026)"},
    {"objectID": "49522897", "title": "Ask HN: Who is hiring? (September 2026)"},
    {"objectID": "49156683", "title": "Ask HN: Who is hiring? (August 2026)"},
]}
HN_SEPTEMBER = {"id": 49522897, "title": "Ask HN: Who is hiring? (September 2026)", "children": [
    {"id": 49522900, "author": "late", "created_at": "2026-10-20T10:00:00.000Z",
     "text": "Orbital Co | Staff Engineer | Remote (EU)"},
]}
HN_STORY = {
    "id": 49922569, "title": "Ask HN: Who is hiring? (October 2026)",
    "children": [
        {"id": 49922609, "author": "mcintyem", "created_at": "2026-10-01T15:03:36.000Z",
         "text": "Sidekick Labs | Senior SWE, R&amp;D Engineer | REMOTE (US Timezones) | Full-time | $170k<p>Sidekick builds AI security software."},
        {"id": 49922700, "author": "gone", "created_at": "2026-10-01T15:05:00.000Z", "text": None},
    ],
}


def stub(routes):
    def handler(request):
        for prefix, body in routes.items():
            if str(request.url).startswith(prefix):
                if isinstance(body, str):
                    return httpx.Response(200, text=body)
                return httpx.Response(200, json=body)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_himalayas_parse():
    p = HimalayasCollector().parse(HIMALAYAS_JOB)
    assert p.external_id == HIMALAYAS_JOB["guid"]
    assert (p.title, p.company_name, p.location_text, p.work_mode) == (
        "Support Analyst", "Cayenta", "Canada", "remote")
    assert (p.salary_min, p.salary_max, p.salary_currency, p.salary_period) == (
        Decimal("60000"), Decimal("75000"), "USD", "year")
    assert p.posted_at == datetime.fromtimestamp(1793096989, UTC)
    assert p.description == "Cayenta , a division of Harris."
    assert "description" not in p.raw


def test_himalayas_salary_fields_only_with_amounts():
    p = HimalayasCollector().parse(dict(HIMALAYAS_JOB, minSalary=None, maxSalary=None))
    assert (p.salary_min, p.salary_currency, p.salary_period) == (None, None, None)


def test_himalayas_pages_and_dedupes_across_queries():
    full_page = {"jobs": [dict(HIMALAYAS_JOB, guid=f"g{i}") for i in range(20)]}
    short_page = {"jobs": [dict(HIMALAYAS_JOB, guid="g0"), dict(HIMALAYAS_JOB, guid="last")]}
    calls = []

    def handler(request):
        calls.append((request.url.params["q"], request.url.params["page"]))
        assert request.url.params["country"] == "Poland"
        return httpx.Response(200, json=full_page if request.url.params["page"] == "1" else short_page)

    collector = HimalayasCollector(
        httpx.Client(transport=httpx.MockTransport(handler)), delay_seconds=0,
        queries=("python", "staff engineer"), max_pages=5,
    )
    postings = list(collector.collect(None))
    assert calls == [("python", "1"), ("python", "2"), ("staff engineer", "1"), ("staff engineer", "2")]
    assert len(postings) == 21


def test_remoteok_skips_legal_notice_and_fixes_encoding():
    postings = list(RemoteOkCollector(stub({"https://remoteok.com/api": REMOTEOK}), 0).collect(None))
    assert [p.external_id for p in postings] == ["1137572", "2"]
    first, second = postings
    assert "Â" not in first.description
    assert (first.salary_min, first.salary_currency, first.salary_period) == (Decimal("100000"), "USD", "year")
    assert second.salary_min is None and second.salary_currency is None


def test_fix_mojibake():
    assert fix_mojibake("cafÃ© â€™") == "café ’"
    assert fix_mojibake("plain text") == "plain text"
    assert fix_mojibake(None) is None


def test_weworkremotely_splits_company_and_title():
    item = ET.fromstring(WWR_RSS).find("channel/item")
    p = WeWorkRemotelyCollector().parse(item)
    assert (p.company_name, p.title) == ("PagerDuty", "Senior Director, Field Enablement")
    assert p.location_text == "Anywhere in the World"
    assert p.posted_at == datetime(2026, 10, 6, 7, 31, 16, tzinfo=UTC)
    assert p.description == "Headquarters: Remote"


def test_weworkremotely_dedupes_across_feeds():
    collector = WeWorkRemotelyCollector(stub({"https://weworkremotely.com/": WWR_RSS}), 0)
    assert len(list(collector.collect(None))) == 1


def test_arbeitnow_keeps_remote_and_poland_only():
    collector = ArbeitnowCollector(stub({"https://www.arbeitnow.com/api": ARBEITNOW}), 0)
    postings = list(collector.collect(None))
    assert [p.external_id for p in postings] == ["staff-engineer-remote-1", "python-dev-warsaw-2"]
    assert postings[0].work_mode == "remote"
    assert postings[0].contract_type is None
    assert postings[1].work_mode == "unknown"


def test_arbeitnow_contract_and_hybrid_title():
    collector = ArbeitnowCollector()
    job = dict(ARBEITNOW["data"][1], title="Customer Success (m/w/d) - Hybrid oder Remote",
               job_types=["Unbefristeter Arbeitsvertrag", "Berufserfahren"])
    p = collector.parse(job)
    assert (p.work_mode, p.contract_type) == ("hybrid", "permanent")


def test_hacker_news_picks_hiring_thread_and_parses_first_line():
    client = stub({
        "https://hn.algolia.com/api/v1/search_by_date": HN_HITS,
        "https://hn.algolia.com/api/v1/items/49922569": HN_STORY,
        "https://hn.algolia.com/api/v1/items/49522897": HN_SEPTEMBER,
        # August must not be fetched; the stub would 404 and fail the run.
    })
    postings = list(HackerNewsHiringCollector(client, 0).collect(None))
    # This month's live post, plus last month's late addition; the deleted comment is skipped.
    assert [x.external_id for x in postings] == ["49922609", "49522900"]
    p = postings[0]
    assert (p.company_name, p.title) == ("Sidekick Labs", "Senior SWE, R&D Engineer")
    assert p.location_text == "US Timezones"
    assert p.raw["terms"] == ["REMOTE (US Timezones)", "Full-time", "$170k"]
    assert p.work_mode == "remote"
    assert p.url == "https://news.ycombinator.com/item?id=49922609"
    assert p.description.endswith("Sidekick builds AI security software.")


@pytest.mark.parametrize("line, company, role, places", [
    ("Sidekick Labs | Senior SWE, R&D Engineer | REMOTE (US Timezones) | Full-time | $170k to $230k",
     "Sidekick Labs", "Senior SWE, R&D Engineer", ["US Timezones"]),
    # No "|": dashes separate fields. The region in "(Remote US)" is kept as a place.
    ("PrairieLearn (Remote US) \u2014 Full-Stack Software Engineer \u2014 Champaign",
     "PrairieLearn", "Full-Stack Software Engineer", ["US", "Champaign"]),
    ("Shepherd (Series B) | ONSITE | San Francisco, CA", "Shepherd", None, ["San Francisco, CA"]),
    ("Acme - Staff Engineer - Berlin - \u20ac90,000 - \u20ac120,000", "Acme", "Staff Engineer", ["Berlin"]),
    # With "|" present, dashes stay inside names and roles.
    ("Descartes - Finale Inventory | Senior Software Engineer | REMOTE | Full-Time",
     "Descartes - Finale Inventory", "Senior Software Engineer", []),
    ("S&T (s&t) | Head of Development \u2013 AI Agent Platform | REMOTE (Germany) | German C1+ required",
     "S&T", "Head of Development \u2013 AI Agent Platform", ["Germany"]),
    # URLs are links, not places; a URL in brackets is stripped from the company.
    ("Mainmatter ( https://mainmatter.com/rust/ ) | REMOTE (EU) | Lead Rust Consultant | 100\u2013140k\u20ac",
     "Mainmatter", "Lead Rust Consultant", ["EU"]),
    ("Thunder Compute (YC S24) | C++ Systems, Infrastructure, BizOps | https://thundercompute.com | SF",
     "Thunder Compute", "C++ Systems, Infrastructure, BizOps", ["SF"]),
])
def test_split_hn_first_line(line, company, role, places):
    fields = split_hn_first_line(line)
    assert (fields["company"], fields["role"], fields["places"]) == (company, role, places)


def test_split_hn_first_line_keeps_links_notes_and_language_needs():
    fields = split_hn_first_line(
        "Hestus (YC W25) | ML Engineer | https://hestus.co/jobs | YC-funded | German C1+ required")
    assert fields["links"] == ["https://hestus.co/jobs"]
    assert fields["notes"] == ["YC W25", "YC-funded"]
    assert fields["language_requirement"] == "German C1+ required"


def test_hn_title_falls_back_to_first_line_without_a_role():
    p = HackerNewsHiringCollector().parse(
        {"id": 1, "created_at": None, "text": "Shepherd (Series B) | ONSITE | San Francisco, CA<p>About us"})
    assert p.title == "Shepherd (Series B) | ONSITE | San Francisco, CA"
    assert p.work_mode == "onsite"


@pytest.mark.parametrize("line, expected", [
    ("Acme | Engineer | REMOTE | $170k to $230k", (170000, 230000, "USD")),
    ("Acme | Engineer | \u20ac90,000 - \u20ac120,000", (90000, 120000, "EUR")),
    ("Acme | Engineer | \u00a380k-\u00a3100k", (80000, 100000, "GBP")),
    ("Mainmatter | Lead | 100\u2013140k\u20ac", (100000, 140000, "EUR")),
    ("Acme | Engineer | 120k-150k EUR", (120000, 150000, "EUR")),
    ("Acme | Engineer | 90.000 - 110.000 \u20ac", (90000, 110000, "EUR")),
    ("Acme | Engineer | 25,000-32,000 PLN", (25000, 32000, "PLN")),
    ("Acme | Engineer | $60-$90/hr", None),
    ("Acme | Engineer | Remote", None),
])
def test_hn_salary(line, expected):
    salary = hn_salary(line)
    if expected is None:
        assert salary == {}
    else:
        assert (salary["salary_min"], salary["salary_max"], salary["salary_currency"]) == (
            Decimal(expected[0]), Decimal(expected[1]), expected[2])
