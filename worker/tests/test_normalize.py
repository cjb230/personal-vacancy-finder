from vacancy_finder.normalize import job_fingerprint, normalize_company, normalize_title


def test_company_suffixes_are_dropped():
    assert normalize_company("Creotech Instruments S.A.") == "creotech instruments"
    assert normalize_company("KP Labs Sp. z o.o.") == "kp labs"
    assert normalize_company("Planet Labs PBC Inc.") == "planet labs pbc"


def test_company_accents_are_folded():
    assert normalize_company("Łódź Robotics GmbH") == "lodz robotics"


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
