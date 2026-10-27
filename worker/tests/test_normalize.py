from vacancy_finder.normalize import (
    detect_language,
    detect_title_language,
    job_fingerprint,
    normalize_company,
    normalize_title,
)


def test_company_suffixes_are_dropped():
    assert normalize_company("Creotech Instruments S.A.") == "creotechinstruments"
    assert normalize_company("KP Labs Sp. z o.o.") == "kplabs"
    assert normalize_company("Planet Labs PBC Inc.") == "planetlabspbc"


def test_polish_legal_forms_and_country_subsidiaries_are_dropped():
    names = ["Nexio Management Sp. z o. o.", "Nexio Management sp.z o.o.", "Nexio Management"]
    assert {normalize_company(n) for n in names} == {"nexiomanagement"}
    assert normalize_company("Antal Sp. z o.o. sp.k.") == "antal"
    assert normalize_company("Capgemini Polska") == normalize_company("Capgemini")
    assert normalize_company("Capgemini Invent") != normalize_company("Capgemini")


def test_spacing_does_not_matter():
    assert normalize_company("Duck Duck Go") == normalize_company("DuckDuckGo")


def test_company_accents_are_folded():
    assert normalize_company("Łódź Robotics GmbH") == "lodzrobotics"


def test_single_word_company_keeps_its_name():
    assert normalize_company("Group") == "group"


def test_title_noise_is_dropped():
    assert normalize_title("Staff Engineer (m/f/d)") == "staff engineer"
    assert normalize_title("Engineering Manager [Remote]") == "engineering manager"
    assert normalize_title("Senior Python Developer - Remote") == "senior python developer"


def test_same_job_on_two_boards_gets_one_fingerprint():
    a = job_fingerprint("ICEYE Oy", "Staff Software Engineer (m/f/d)")
    b = job_fingerprint("ICEYE", "Staff Software Engineer")
    assert a == b


def test_different_titles_get_different_fingerprints():
    assert job_fingerprint("ICEYE", "Staff Engineer") != job_fingerprint(
        "ICEYE", "Engineering Manager"
    )


def test_more_languages_detected():
    assert detect_language("Buscamos un ingeniero para el equipo de trabajo con experiencia") == "es"
    assert detect_language("Você vai trabalhar com a nossa equipe, experiência em dados") == "pt"
    assert detect_language("Nous recherchons pour notre équipe une personne avec expérience") == "fr"
    assert detect_language("We are looking for an engineer to join our team with experience") == "en"


def test_polish_letters_in_title():
    assert detect_title_language("Specjalista ds. księgowości") == "pl"
    assert detect_title_language("Thermal Engineer") is None


def test_per_city_copies_of_a_title_match():
    titles = ["Senior Software Engineer - Berlin", "Senior Software Engineer, London",
              "Senior Software Engineer – New York, NY", "Senior Software Engineer in Warsaw"]
    assert {normalize_title(t) for t in titles} == {"senior software engineer"}
    assert normalize_title("Software Engineer - Platform") == "software engineer platform"
