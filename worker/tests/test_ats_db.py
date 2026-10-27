"""ATS collectors against a real database, with HTTP answered by a stub transport."""

import json
import textwrap
from pathlib import Path

import httpx
import pytest

from vacancy_finder.collectors.ats import GreenhouseCollector
from vacancy_finder.runner import run_collector
from vacancy_finder.seeds import load_companies

from .test_store import DB_URL, conn, count  # noqa: F401  (conn is a fixture)

pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

GREENHOUSE = json.loads((Path(__file__).parent / "fixtures" / "greenhouse.json").read_text())


def write_seeds(tmp_path, body):
    path = tmp_path / "companies.csv"
    path.write_text("name,sectors,ats_kind,ats_slug,website,notes\n" + textwrap.dedent(body))
    return path


def stub_client(responses):
    def handler(request):
        slug = request.url.path.split("/")[3]
        status, body = responses[slug]
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_seed_file_creates_tracked_companies(conn, tmp_path):  # noqa: F811
    path = write_seeds(tmp_path, """\
        Acme Space,space;eo,greenhouse,acme,https://acme.example,
        Untracked Co,robotics,,,,
    """)
    assert load_companies(conn, path) == 2
    rows = conn.execute("SELECT name, sectors, tracked FROM companies ORDER BY name").fetchall()
    assert rows == [("Acme Space", ["space", "eo"], True), ("Untracked Co", ["robotics"], False)]
    load_companies(conn, path)  # loading twice is harmless
    assert count(conn, "SELECT count(*) FROM companies") == 2


def test_one_failing_company_does_not_close_its_jobs(conn, tmp_path):  # noqa: F811
    load_companies(conn, write_seeds(tmp_path, """\
        Acme Space,space,greenhouse,acme,,
        Beta Orbital,space,greenhouse,beta,,
    """))
    ok = {"acme": (200, GREENHOUSE), "beta": (200, GREENHOUSE)}
    run_collector(conn, GreenhouseCollector(stub_client(ok)))
    assert count(conn, "SELECT count(*) FROM postings") == 4
    assert count(conn, "SELECT count(*) FROM jobs") == 4

    beta_down = {"acme": (200, {"jobs": []}), "beta": (503, {})}
    run_collector(conn, GreenhouseCollector(stub_client(beta_down)))
    # Acme's jobs really went away; Beta's are kept because its feed failed.
    open_by_company = conn.execute(
        """
        SELECT c.name, count(*) FROM postings p JOIN jobs j ON j.id = p.job_id
        JOIN companies c ON c.id = j.company_id
        WHERE p.closed_at IS NULL GROUP BY c.name
        """
    ).fetchall()
    assert open_by_company == [("Beta Orbital", 2)]
    assert conn.execute(
        "SELECT status FROM collection_runs ORDER BY id DESC LIMIT 1"
    ).fetchone() == ("ok",)


def test_run_fails_when_every_company_fails(conn, tmp_path):  # noqa: F811
    load_companies(conn, write_seeds(tmp_path, "Acme Space,space,greenhouse,acme,,\n"))
    run_collector(conn, GreenhouseCollector(stub_client({"acme": (500, {})})))
    status, error = conn.execute(
        "SELECT status, error FROM collection_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert status == "failed" and "every greenhouse company failed" in error
