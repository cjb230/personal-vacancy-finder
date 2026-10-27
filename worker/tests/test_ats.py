"""Parsing tests for the ATS collectors, using fixtures shaped like each public feed."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from vacancy_finder.collectors.ats import (
    AshbyCollector,
    GreenhouseCollector,
    LeverCollector,
    TrackedCompany,
    WorkableCollector,
    html_to_text,
)

FIXTURES = Path(__file__).parent / "fixtures"
ACME = TrackedCompany(id=1, name="Acme Space", slug="acme")


def parse(collector, fixture):
    return list(collector.parse(json.loads((FIXTURES / fixture).read_text()), ACME))


def test_greenhouse():
    first, second = parse(GreenhouseCollector(), "greenhouse.json")
    assert first.external_id == "acme:4012345"
    assert first.title == "Staff Software Engineer, Ground Segment"
    assert first.company_name == "Acme Space"
    assert first.work_mode == "hybrid"
    assert first.description == "Build & run our ground segment .\nPython"
    assert first.posted_at == datetime(2026, 10, 1, 13, 0, tzinfo=UTC)
    assert (first.ats_kind, first.ats_slug) == ("greenhouse", "acme")
    assert second.work_mode == "remote"
    assert second.description is None


def test_lever():
    first, second = parse(LeverCollector(), "lever.json")
    assert first.location_text == "Gdańsk; Warsaw"
    assert first.work_mode == "hybrid"
    assert first.contract_type == "Full-time"
    assert (first.salary_min, first.salary_max) == (Decimal("25000"), Decimal("32000"))
    assert (first.salary_currency, first.salary_period) == ("PLN", "month")
    assert second.work_mode == "remote"  # 'unspecified' falls back to the location text
    assert second.description == "Lead us"


def test_ashby_skips_unlisted_jobs():
    postings = parse(AshbyCollector(), "ashby.json")
    assert [p.external_id for p in postings] == ["acme:0f1e", "acme:2a3b"]
    first, second = postings
    assert first.location_text == "Berlin; Warsaw"
    assert first.work_mode == "hybrid"
    assert second.work_mode == "remote"
    assert second.description == "Remote across the EU"


def test_workable():
    first, second = parse(WorkableCollector(), "workable.json")
    assert first.external_id == "acme:AB12CD34EF"
    assert first.location_text == "Warsaw, Poland"
    assert first.work_mode == "unknown"
    assert second.location_text == "Poland"
    assert second.work_mode == "remote"
    assert second.contract_type is None


@pytest.mark.parametrize(
    "fragment, expected",
    [(None, None), ("<p>a</p><p>b</p>", "a\nb"), ("x&nbsp;&amp;&nbsp;y", "x & y")],
)
def test_html_to_text(fragment, expected):
    assert html_to_text(fragment) == expected
