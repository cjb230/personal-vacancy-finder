"""Scoring jobs against the search criteria."""

from decimal import Decimal

import pytest

from vacancy_finder.matching import (JobFacts, b2b_take_home, monthly_pln, required_languages,
                                     score_job, take_home, uop_take_home)


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
    facts.take_home_max = pay
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
    (("remote",), ("United States",), False),
    (("remote",), ("Remote - US only",), False),
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
    assert "take-home only up to ~15,000 PLN/month (-15)" in score_job(
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


@pytest.mark.parametrize("modes, locations", [
    (("unknown",), ("San Francisco, CA",)),
    (("hybrid",), ("Abu Dhabi",)),
    (("remote",), ("Boulder, CO",)),
    (("remote",), ("United States",)),
])
def test_outside_europe_is_a_maybe_not_ruled_out(modes, locations):
    result = score_job(job("Staff Python Engineer", modes=modes, locations=locations,
                           sectors=["space"], pay=Decimal(40000)))
    assert result.verdict == "maybe"


def test_remote_anywhere_and_time_zone_overlap():
    anywhere = score_job(job("Staff Python Engineer", locations=("Anywhere",), sectors=["space"]))
    assert anywhere.verdict == "match"
    overlap = score_job(job("Staff Python Engineer", locations=("Anywhere",), sectors=["space"],
                            description="Python and SQL. Some overlap with PST required."))
    assert "asks for overlap with US/other time zones: check the hours" in overlap.reasons
    assert overlap.verdict == "match"


def test_region_in_title_is_a_maybe():
    result = score_job(job("Data Engineering Tech Lead (LatAm Only)", locations=("Anywhere",),
                           sectors=["space"]))
    assert result.verdict == "maybe"


def test_european_country_codes_are_not_us_states():
    assert score_job(job("Staff Python Engineer", modes=("hybrid",), locations=("Munich, DE",))
                     ).verdict != "no"


def test_remote_within_another_country_and_hybrid_abroad_capped():
    spain = score_job(job("Engineering Director", modes=("remote",), locations=("Spain",),
                          sectors=["space"]))
    assert spain.verdict == "maybe"
    assert any("remote within Spain" in r for r in spain.reasons)
    berlin = score_job(job("Engineering Manager, Platform", modes=("hybrid",),
                           locations=("Berlin, Germany",), sectors=["space"], pay=Decimal(40000)))
    assert berlin.verdict == "maybe"


def test_take_home_pay():
    assert b2b_take_home(Decimal(35280)) == Decimal("29040.70")
    # A permanent contract at 10,000 gross is about 7,150 a month in hand.
    assert round(uop_take_home(Decimal(10000))) == 7147
    # Pay is UoP unless the posting says B2B or contractor.
    assert take_home(Decimal(31000), "permanent") == take_home(Decimal(31000), None)
    assert take_home(Decimal(31000), "b2b;permanent") == b2b_take_home(Decimal(31000))
    assert take_home(Decimal(31000), "Contractor") == b2b_take_home(Decimal(31000))
    assert take_home(Decimal(31000), None) < b2b_take_home(Decimal(31000))


@pytest.mark.parametrize("title", [
    "Java Expert/ Tech Lead", "Lead Software Engineer (Golang/TypeScript)",
    "Mobile Staff Engineer, Platform/Infrastructure", "Technical Lead (C++, Linux)",
    "Veeva Vault Tech Lead", "Avaloq Engineering Lead",
])
def test_another_stack_in_the_title_costs_points(title):
    assert "title names another stack or platform (-15)" in score_job(job(title)).reasons


def test_python_in_the_title_is_not_another_stack():
    reasons = score_job(job("Staff Full Stack Engineer (Python/React)")).reasons
    assert "title names another stack or platform (-15)" not in reasons
    assert "title names another stack or platform (-15)" not in score_job(
        job("Engineering Manager, Go-to-Market Platform")).reasons


@pytest.mark.parametrize("title", [
    "Senior / Principal DevOps Engineer", "Tech Lead DevOps Engineer (f/m/x)", "Staff SRE",
])
def test_ops_roles_cost_points(title):
    assert "DevOps / SRE / sysadmin role (-15)" in score_job(job(title)).reasons


def test_region_from_headline_when_no_location():
    result = score_job(job("Senior/Staff Backend Engineer, Backend Platform", locations=(),
                           sectors=["space"], description=(
                               "Remote (US) Close ( https://close.com ) | Senior/Staff Backend "
                               "Engineer | Full-time\nWe use Python and PostgreSQL.")))
    assert result.verdict == "maybe"
    assert "remote, listed for US (+5)" in result.reasons
    # "Join us" in a headline isn't a region.
    assert not any("listed for" in r for r in score_job(job(
        "Staff Python Engineer", locations=(), description="Remote. Join us!\nPython.")).reasons)


def test_remote_in_warsaw_counts_as_poland():
    assert "remote, Europe/Poland OK (+15)" in score_job(job(
        "Technical Lead", locations=("Warsaw",))).reasons


def test_satellite_needs_a_space_word_nearby():
    bank = "Python. Ensure satellite interfaces are built optimally."
    assert not any("space" in r for r in score_job(job("Staff Software Engineer",
                                                       description=bank)).reasons)
    assert "space / satellite / GIS work (+15)" in score_job(job(
        "Staff Software Engineer", description="Python software for satellites in orbit.")).reasons
