from decimal import Decimal
from pathlib import Path

from vacancy_finder.collectors.justjoinit import (
    JustJoinItCollector,
    SitemapEntry,
    find_offer,
    parse_offer_page,
    parse_sitemap,
    pick_salary,
)
from vacancy_finder.normalize import looks_polish

FIXTURES = Path(__file__).parent / "fixtures"
SLUG = "shelf-senior-backend-python-engineer-warszawa-python-a3b545e2"
ENTRY = SitemapEntry(f"https://justjoin.it/job-offer/{SLUG}", "2026-10-25")


def page():
    return (FIXTURES / "justjoinit_offer.html").read_text()


def test_sitemap_index_and_urlset():
    children, entries = parse_sitemap((FIXTURES / "justjoinit_sitemap_index.xml").read_bytes())
    assert children == ["https://justjoin.it/sitemaps/active-jobs/part0.xml"]
    assert entries == []
    children, entries = parse_sitemap((FIXTURES / "justjoinit_sitemap_part0.xml").read_bytes())
    assert children == []
    assert entries[0] == ENTRY
    assert entries[0].slug == SLUG


def test_keyword_filter():
    _, entries = parse_sitemap((FIXTURES / "justjoinit_sitemap_part0.xml").read_bytes())
    wanted = [e.slug for e in entries if JustJoinItCollector().wanted(e)]
    assert wanted == [SLUG, "beta-engineering-manager-krakow-java-87654321"]


def test_find_offer_matches_slug_not_similar_offers():
    offer = find_offer(page(), SLUG)
    assert offer["guid"] == "015c71ad-b203-4cd6-8a7a-d40962154c32"
    assert find_offer(page(), "acme-python-developer-krakow-python-ffff0000") is None


def test_parse_offer_page():
    p = parse_offer_page(page(), ENTRY)
    assert p.external_id == SLUG
    assert p.title == "Senior Backend Python Engineer"
    assert p.company_name == "Shelf"
    assert p.location_text == "Warszawa"
    assert p.work_mode == "onsite"
    assert p.contract_type == "b2b"
    # The employer entered USD; PLN and EUR are the site's conversions.
    assert (p.salary_min, p.salary_max, p.salary_currency, p.salary_period) == (
        Decimal("8000"), Decimal("12000"), "USD", "month"
    )
    assert p.language == "en"
    assert p.description.startswith("About Shelf")
    assert p.raw["sitemap_lastmod"] == "2026-10-25"


def test_pick_salary_prefers_b2b_among_originals():
    types = [
        {"type": "permanent", "from": 20000, "to": 25000, "currency": "PLN", "currencySource": "original"},
        {"type": "b2b", "from": 26000, "to": 32000, "currency": "PLN", "currencySource": "original"},
        {"type": "b2b", "from": 6000, "to": 7000, "currency": "EUR", "currencySource": "conversion"},
    ]
    assert pick_salary(types)["from"] == 26000
    assert pick_salary([]) is None


def test_page_without_offer_data():
    assert parse_offer_page("<html></html>", ENTRY) is None


def test_looks_polish():
    assert looks_polish("Wymagania: doświadczenie w Python oraz znajomość SQL. Oferujemy pracę w zespole.")
    assert not looks_polish("You will lead the team and own the platform. Experience with Python.")
    assert not looks_polish(None)
