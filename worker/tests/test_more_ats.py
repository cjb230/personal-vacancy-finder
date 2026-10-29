"""Parsing tests for the Personio, Teamtailor, Recruitee, Traffit and careers-page collectors."""

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from vacancy_finder.collectors.ats import TrackedCompany
from vacancy_finder.collectors.more_ats import (
    PersonioCollector,
    RecruiteeCollector,
    TeamtailorCollector,
    TraffitCollector,
)
from vacancy_finder.collectors.page import PageCollector

FIXTURES = Path(__file__).parent / "fixtures"


def company(slug, **extra):
    return TrackedCompany(id=1, name="Acme Space", slug=slug, **extra)


def test_personio():
    payload = ET.fromstring((FIXTURES / "personio.xml").read_bytes())
    first, second = PersonioCollector().parse(payload, company("acme.jobs.personio.de"))
    assert first.external_id == "acme.jobs.personio.de:1234567"
    assert first.url == "https://acme.jobs.personio.de/job/1234567"
    assert first.title == "Engineering Manager, Geospatial Platform"
    assert first.location_text == "Berlin (Remote)"
    assert first.work_mode == "remote"
    assert first.description == "Your mission\nLead a team building satellite analytics.\nPython\nPostgreSQL"
    assert first.contract_type == "permanent"
    assert first.posted_at == datetime(2026, 10, 2, 9, 15, tzinfo=UTC)
    assert first.raw["yearsOfExperience"] == "5-7"
    assert (first.ats_kind, first.ats_slug) == ("personio", "acme.jobs.personio.de")
    assert second.title == "Working Student R&D"
    assert second.description is None
    assert second.work_mode == "unknown"


def test_teamtailor():
    payload = ET.fromstring((FIXTURES / "teamtailor.rss").read_bytes())
    first, second = TeamtailorCollector().parse(payload, company("jobs.acme.com"))
    assert first.external_id == "jobs.acme.com:https://jobs.acme.com/jobs/555-staff-data-engineer"
    assert first.url == "https://jobs.acme.com/jobs/555-staff-data-engineer"
    assert first.title == "Staff Data Engineer"
    assert first.location_text == "Stockholm, Sweden; Berlin, Germany"
    assert first.work_mode == "hybrid"
    assert first.description == "Trade power on energy markets .\nPython & SQL"
    assert first.posted_at == datetime(2026, 10, 6, 8, 30, tzinfo=timezone(timedelta(hours=2)))
    assert first.raw["department"] == "Data"
    assert second.work_mode == "remote"
    assert second.location_text is None
    assert second.description is None


def test_teamtailor_feed_url_keeps_path():
    assert TeamtailorCollector().feed_url("www.engineering-jobs.sener/en") == (
        "https://www.engineering-jobs.sener/en/jobs.rss"
    )


def test_recruitee():
    payload = json.loads((FIXTURES / "recruitee.json").read_text())
    first, second = RecruiteeCollector().parse(payload, company("acme"))
    assert first.external_id == "acme:901"
    assert first.url == "https://acme.recruitee.com/o/head-of-engineering"
    assert first.location_text == "Berlin, Germany; Warsaw, Poland"
    assert first.work_mode == "remote"
    assert first.description == "Lead our EO data platform.\n\nPython"
    assert (first.salary_min, first.salary_max) == (Decimal("90000"), Decimal("120000"))
    assert (first.salary_currency, first.salary_period) == ("EUR", "year")
    assert first.posted_at == datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
    assert second.url == "https://acme.recruitee.com/o/office-manager"
    assert second.location_text == "Berlin, Germany"
    assert second.work_mode == "onsite"
    assert second.salary_currency is None
    assert second.description is None


def test_traffit():
    payload = json.loads((FIXTURES / "traffit.json").read_text())
    first, second = TraffitCollector().parse(payload, company("acme"))
    assert first.external_id == "acme:41"
    assert first.url == "https://acme.traffit.com/public/job_posts/published/41"
    assert first.title == "Python Team Lead"
    assert first.location_text == "Warszawa, Poland"
    assert first.work_mode == "remote"
    assert first.description == "Cloud for EO data ."
    assert first.language == "en"
    assert first.contract_type == "B2B"
    assert first.posted_at == datetime(2026, 10, 3, 10, 20)
    assert first.closes_at is None
    assert second.language == "pl"
    assert second.work_mode == "unknown"
    assert second.description is None


def test_traffit_valid_end_is_warsaw_time():
    payload = json.loads((FIXTURES / "traffit.json").read_text())
    for post in payload:
        post["valid_end"] = "2026-11-02 23:59:00"
    first, _ = TraffitCollector().parse(payload, company("acme"))
    assert first.closes_at == datetime(2026, 11, 2, 22, 59, tzinfo=UTC)
    assert first.closes_kind == "expiry"


def parse_page(fixture, slug, pattern):
    collector = PageCollector()
    return list(collector.parse((FIXTURES / fixture).read_text(), company(slug, link_pattern=pattern)))


def test_page_title_in_sibling_heading():
    postings = parse_page("page_kplabs.html", "https://www.kplabs.space/career", r"^/jobs/[^/?#]+$")
    assert [(p.title, p.url) for p in postings] == [
        ("Accounting Specialist", "https://www.kplabs.space/jobs/accounting-specialist"),
        ("Head of Thermal and Mechanical Engineering",
         "https://www.kplabs.space/jobs/head-of-thermal-and-mechanical-engineering"),
    ]
    assert postings[0].external_id == postings[0].url
    assert (postings[0].ats_kind, postings[0].ats_slug) == ("page", "https://www.kplabs.space/career")


def test_page_title_in_link():
    postings = parse_page("page_creotech.html", "https://creotech.pl/en/career", r"^/en/job-offers/[^/?#]+$")
    assert [p.title for p in postings] == ["Avionics Architect", "FPGA Engineer"]
    assert postings[1].url == "https://creotech.pl/en/job-offers/fpga-engineer"


def test_page_location():
    (posting,) = parse_page("page_piap.html", "https://piap.space/job-openings/",
                            r"^https://piap\.space/jobs/[^?#]+/$")
    assert posting.title == "Electronics Engineer"
    assert posting.location_text == "Warsaw"
    assert posting.url == "https://piap.space/jobs/electronics-engineer/"


def test_page_needs_pattern():
    with pytest.raises(ValueError):
        list(PageCollector().parse("<html></html>", company("https://x.example/careers")))
