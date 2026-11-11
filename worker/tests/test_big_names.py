"""The list of well-known employers, and the bonuses it and LinkedIn alerts give."""

import os

import pytest

from vacancy_finder import big_names
from vacancy_finder.matching import score_all
from vacancy_finder.models import RawPosting
from vacancy_finder.store import ensure_source, upsert_posting

from .test_store import DB_URL, conn  # noqa: F401  (conn is a fixture)

LIST = "name,aliases\nGoogle,Alphabet;Google DeepMind\nApple,\n"


def test_load_names_and_aliases(tmp_path):
    file = tmp_path / "big_names.csv"
    file.write_text(LIST)
    keys = big_names.load(file)
    assert keys == {"google": "Google", "alphabet": "Google", "googledeepmind": "Google",
                    "apple": "Apple"}
    # Matching is on normalised names: case, legal suffixes and country words don't matter...
    from vacancy_finder.normalize import normalize_company
    assert keys.get(normalize_company("Google Poland Sp. z o.o.")) == "Google"
    assert keys.get(normalize_company("Apple Inc.")) == "Apple"
    # ...but it is exact, so another company that merely starts the same way isn't matched.
    assert normalize_company("Apple Leisure Group") not in keys
    assert big_names.load(tmp_path / "missing.csv") == {}


def test_list_sits_next_to_the_seed_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SEEDS_FILE", str(tmp_path / "companies.csv"))
    assert big_names.path() == tmp_path / "big_names.csv"


def test_shipped_list_starts_with_faang():
    names = set(big_names.load(big_names.path().parent / "big_names.csv").values())
    assert {"Meta", "Apple", "Amazon", "Netflix", "Google"} <= names


@pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")
def test_scoring_gives_both_bonuses(conn, monkeypatch, tmp_path, capsys):  # noqa: F811
    (tmp_path / "big_names.csv").write_text(LIST)
    monkeypatch.setenv("SEEDS_FILE", str(tmp_path / "companies.csv"))
    email = ensure_source(conn, "email_alerts", "Job-alert emails", "email")
    board = ensure_source(conn, "test", "Test board", "board")
    upsert_posting(conn, email, RawPosting(
        external_id="linkedin:1", url="https://www.linkedin.com/jobs/view/1/",
        title="Engineering Manager", company_name="Google Poland", location_text="Remote",
        work_mode="remote", raw={"platform": "linkedin", "alert_from": "LinkedIn"}))
    upsert_posting(conn, board, RawPosting(
        external_id="2", url="https://x.example/2", title="Staff Engineer",
        company_name="Apple Leisure Group", work_mode="remote"))
    conn.commit()
    score_all(conn)
    reasons = dict(conn.execute(
        "SELECT j.title, s.reasons FROM job_scores s JOIN jobs j ON j.id = s.job_id").fetchall())
    assert "found on LinkedIn (+5)" in reasons["Engineering Manager"]
    assert "well-known company: Google (+5)" in reasons["Engineering Manager"]
    assert not any("LinkedIn" in r or "well-known" in r for r in reasons["Staff Engineer"])

    big_names.check(conn, big_names.load(tmp_path / "big_names.csv"))
    out = capsys.readouterr().out
    assert "Google Poland (1 open jobs) = Google" in out
    assert "Apple Leisure Group (1 open jobs)" in out.split("not matching")[1]
