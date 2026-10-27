"""Scoring jobs against the search criteria."""

from decimal import Decimal

import pytest

from vacancy_finder.matching import JobFacts, monthly_pln, required_languages, score_job


def job(title, *, description="We use Python and PostgreSQL. You will lead the team.", company="Acme",
        sectors=(), modes=("remote",), locations=("Europe",), languages=("en",), required=(),
        contracts=(), pay=None, excluded=False):
    facts = JobFacts(1, title, company, list(sectors), excluded)
    facts.work_modes = set(modes)
    facts.locations = list(locations)
    facts.descriptions = [description] if description else []
    facts.languages = set(languages)
    facts.required_languages = set(required)
    facts.contract_types = set(contracts)
    facts.monthly_pln_max = pay
    return facts


def test_ideal_job_matches():
    result = score_job(job("Engineering Manager, Ground Segment", sectors=["space"],
                           contracts=["b2b"], pay=Decimal(40000)))
    assert result.verdict == "match"
    assert result.role_level == "manager"
    assert "space / Earth observation company (+25)" in result.reasons


@pytest.mark.parametrize("title, level", [
    ("Staff Software Engineer", "staff"),
    ("Principal Python Engineer", "staff"),
    ("Head of Engineering", "director"),
    ("Tech Lead, Platform", "manager"),
    ("Senior Backend Engineer", "senior"),
])
def test_role_levels(title, level):
    assert score_job(job(title)).role_level == level


@pytest.mark.parametrize("title", [
    "Account Executive Central and Eastern Europe", "Head of Sales", "Senior Product Designer",
    "Financial Controlling Specialist", "Working Student R&D", "Office Manager",
])
def test_non_engineering_roles_are_out(title):
    result = score_job(job(title))
    assert result.verdict == "no"
    assert "not an engineering role" in result.reasons


def test_director_asking_for_director_experience_loses_points():
    plain = score_job(job("Director of Engineering", description="Python and PostgreSQL."))
    demanding = score_job(job("Director of Engineering", description=(
        "Python and PostgreSQL. 5+ years of experience as a Director of Engineering.")))
    assert demanding.score == plain.score - 20


def test_polish_advert_or_polish_requirement_is_out():
    assert score_job(job("Staff Engineer", languages=("pl",))).reasons[0] == "advert is in Polish"
    assert score_job(job("Staff Engineer", required=("en", "pl"))).reasons[0] == "requires Polish"
    result = score_job(job("Staff Engineer", description="Python. Fluent German is required."))
    assert result.verdict == "no" and result.reasons[0].startswith("asks for another language")
    # English advert with unknown or mixed language tags is fine.
    assert score_job(job("Staff Engineer", languages=(None,))).verdict != "no"
    assert score_job(job("Staff Engineer", languages=("en", "pl"))).verdict != "no"


@pytest.mark.parametrize("modes, locations, verdict_no", [
    (("remote",), ("United States",), True),
    (("remote",), ("Remote - US only",), True),
    (("remote",), ("EMEA",), False),
    (("remote",), (), False),
    (("hybrid",), ("Warsaw, Poland",), False),
    (("onsite",), ("Munich",), True),
    (("onsite",), ("Warszawa",), False),
])
def test_location_rules(modes, locations, verdict_no):
    result = score_job(job("Staff Python Engineer", modes=modes, locations=locations))
    assert (result.verdict == "no") == verdict_no


def test_excluded_sector():
    result = score_job(job("Staff Engineer", description="Python for our online casino platform"))
    assert result.verdict == "no"
    assert score_job(job("Staff Engineer", excluded=True)).verdict == "no"


def test_low_pay_costs_points():
    assert "pay only up to ~15,000 PLN/month (-15)" in score_job(
        job("Staff Engineer", pay=Decimal(15000))).reasons


def test_monthly_pln():
    assert monthly_pln(Decimal(200), "PLN", "hour") == 33600
    assert monthly_pln(Decimal(120000), "EUR", "year") == 43000
    assert monthly_pln(Decimal(100), "XYZ", "hour") is None


def test_required_languages_from_sources():
    assert required_languages("justjoinit", {"offer": {"languages": [{"code": "pl"}, {"code": "en"}]}}) == {"pl", "en"}
    assert required_languages("hn-whoishiring", {"language_requirement": "German C1+ required"}) == {"de"}
    assert required_languages("greenhouse", {}) == set()


def test_benefit_words_dont_count_as_sector():
    text = "Python and PostgreSQL. A leader in this space, with health insurance and an education budget."
    reasons = score_job(job("Staff Software Engineer", description=text)).reasons
    assert not any("space" in r or "energy" in r for r in reasons)
    assert "space / satellite / GIS work (+15)" in score_job(job(
        "Staff Software Engineer", description="Python for our Earth observation satellites.")).reasons


def test_bank_vp_grade_is_not_director():
    assert score_job(job("Python Credit Risk Senior Developer - VP")).role_level == "senior"
    assert score_job(job("Product Management Director")).verdict == "no"


def test_multi_role_hn_post_judged_on_engineering_role():
    result = score_job(job("Director of Corp Dev · Product Marketing Associate · MLOps Engineer"))
    assert result.role_level == "other"


def test_region_limit_in_title():
    result = score_job(job("Data Engineering Tech Lead (LatAm Only)", locations=("Anywhere",)))
    assert result.verdict == "no"


def test_unknown_mode_abroad_is_probably_onsite():
    reasons = score_job(job("Staff Software Engineer", modes=("unknown",),
                            locations=("Parsdorf, Bavaria",))).reasons
    assert "in Parsdorf, Bavaria, work mode not stated: probably on-site (-10)" in reasons


def test_senior_and_non_software_roles_capped_at_maybe():
    senior = score_job(job("Senior Python Engineer", sectors=["space"], pay=Decimal(40000)))
    assert senior.verdict == "maybe"
    propulsion = score_job(job("Principal Propulsion Engineer", sectors=["space"]))
    assert propulsion.verdict == "maybe"
    architect = score_job(job("Space Mission Architect", description=None, modes=("unknown",),
                              locations=("Warsaw",), sectors=["space"]))
    assert architect.verdict == "match"


def test_content_systems_is_engineering():
    assert score_job(job("Senior Principal Engineer - AI Content Systems")).verdict != "no"
