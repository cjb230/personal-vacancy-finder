"""Scoring stored jobs and the results page, against a real database."""

import os
import re
from datetime import UTC, datetime

import pytest

from vacancy_finder import web
from vacancy_finder.matching import score_all
from vacancy_finder.models import RawPosting
from vacancy_finder.store import close_unseen, confirm_tracked_jobs, ensure_source, upsert_posting

from .test_store import DB_URL, conn, count  # noqa: F401  (conn is a fixture)

pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


def test_score_render_and_review(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    source = ensure_source(conn, "test", "Test board", "board")
    upsert_posting(conn, source, RawPosting(
        external_id="1", url="https://x.example/1", title="Engineering Manager <Python>",
        company_name="Orbit & Co", location_text="Remote, Europe", work_mode="remote",
        description="Python and PostgreSQL for satellite imagery. You will lead the team."))
    upsert_posting(conn, source, RawPosting(
        external_id="2", url="https://x.example/2", title="Account Executive",
        company_name="Acme", work_mode="remote", description="Sell things to people we know."))
    conn.commit()

    assert score_all(conn) == {"match": 1, "no": 1}
    page = web.render("match", "", 0)
    assert "Engineering Manager &lt;Python&gt;" in page  # escaped
    assert "Matches (1)" in page and "Filtered out (1)" in page

    job_id = conn.execute("SELECT id FROM jobs WHERE title LIKE 'Engineering%'").fetchone()[0]
    web.save_review(job_id, "interested")
    assert "Matches (0)" in web.render("match", "", 0)
    assert "Engineering Manager" in web.render("interested", "", 0)
    assert "Account Executive" in web.render("no", "acme", 0)
    web.save_review(job_id, "")
    assert count(conn, "SELECT count(*) FROM job_reviews") == 0


def test_feedback_keeps_a_snapshot_of_the_ad(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    source = ensure_source(conn, "test", "Test board", "board")
    upsert_posting(conn, source, RawPosting(
        external_id="1", url="https://x.example/1", title="Engineering Manager",
        company_name="Orbit", location_text="Remote, Europe", work_mode="remote",
        description="Python and PostgreSQL for satellite imagery. You will lead the team."))
    conn.commit()
    score_all(conn)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]

    web.save_feedback(job_id, "rate_higher", "space & GIS")
    web.save_feedback(job_id, "score_lower")
    page = web.render("match", "", 0)
    assert "Rate higher: space &amp; GIS" in page and "Score lower" in page
    assert "Reject for:" in page

    web.save_feedback(job_id, "reject", "needs German")
    assert "Matches (0)" in web.render("match", "", 0)
    assert "Rejected: needs German" in web.render("not_interested", "", 0)

    # The snapshot outlives later changes to the job and its postings.
    conn.execute("UPDATE postings SET description = 'changed'")
    conn.commit()
    ad_text, postings, score, reasons = conn.execute(
        "SELECT ad_text, postings, score, reasons FROM job_feedback WHERE kind = 'reject'").fetchone()
    assert ad_text.startswith("Python and PostgreSQL")
    assert postings[0]["url"] == "https://x.example/1" and postings[0]["description"] == ad_text
    assert score > 0 and reasons

    feedback_id = conn.execute("SELECT id FROM job_feedback WHERE kind = 'score_lower'").fetchone()[0]
    web.delete_feedback(feedback_id)
    assert count(conn, "SELECT count(*) FROM job_feedback") == 2


def test_filters_by_first_seen_and_score(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    source = ensure_source(conn, "test", "Test board", "board")
    for n, title in (("1", "Engineering Manager"), ("2", "Staff Python Engineer")):
        upsert_posting(conn, source, RawPosting(
            external_id=n, url=f"https://x.example/{n}", title=title, company_name="Orbit",
            location_text="Remote, Europe", work_mode="remote",
            description="Python and PostgreSQL for satellite imagery. You will lead the team."))
    conn.execute("UPDATE jobs SET first_seen_at = '2026-01-10 09:30+00' WHERE title LIKE 'Staff%'")
    conn.commit()
    score_all(conn)
    old_score = conn.execute(
        "SELECT score FROM job_scores s JOIN jobs j ON j.id = s.job_id WHERE j.title LIKE 'Staff%'"
    ).fetchone()[0]

    everything = web.render("match", "", 0)
    assert "Engineering Manager" in everything and "Staff Python Engineer" in everything
    assert "first seen 10 Jan 10:30" in everything  # shown in Warsaw time

    recent = web.render("match", "", 0, seen="24")
    assert "Engineering Manager" in recent and "Staff Python Engineer" not in recent
    assert "Matches (1)" in recent and "seen=24" in recent  # tabs keep the filter
    # The period chips: the chosen one lit, each a one-click link that keeps the other filters.
    chips = recent.split('class="chips seen"')[1].split("</nav>")[0]
    assert chips.count('aria-current="true"') == 1
    assert 'class="on" aria-current="true" href="/?view=match&q=&seen=24">Last 24 hours' in chips
    assert 'href="/?view=match&q=&seen=72">Last 3 days' in chips
    assert 'href="/?view=match&q=">Any time' in chips
    assert 'name="seen" value="24"' in recent  # the filters form carries the period
    page = web.render("match", "", 0, since="2026-01-10T10:00", min_score="5")
    chips = page.split('class="chips seen"')[1].split("</nav>")[0]
    assert 'aria-current="true"' not in chips  # a "since" time isn't "Any time"
    assert 'href="/?view=match&q=&min_score=5&seen=168">Last 7 days' in chips  # drops since
    since = web.render("match", "", 0, since="2026-01-10T10:00")  # Warsaw, so 09:00 UTC
    assert "Staff Python Engineer" in since
    assert "Staff Python Engineer" not in web.render("match", "", 0, since="2026-01-10T11:00")

    assert "Staff Python Engineer" not in web.render("match", "", 0, min_score=str(old_score + 100))
    newest = web.render("match", "", 0, sort="new")
    assert newest.index("Engineering Manager") < newest.index("Staff Python Engineer")
    assert "Matches (2)" in web.render("match", "", 0, seen="bogus", since="nope", min_score="x")


def test_closing_filter_and_sort(conn, monkeypatch):  # noqa: F811
    from datetime import UTC, datetime, timedelta

    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    source = ensure_source(conn, "test", "Test board", "board")
    now = datetime.now(UTC)
    for n, title, closes, kind in (
            ("1", "Engineering Manager", now + timedelta(hours=30), "deadline"),
            ("2", "Staff Python Engineer", now + timedelta(days=5), "expiry"),
            ("3", "Python Engineering Lead", now - timedelta(days=1), "deadline"),
            ("4", "Head of Engineering", None, None)):
        upsert_posting(conn, source, RawPosting(
            external_id=n, url=f"https://x.example/{n}", title=title, company_name=f"Orbit {n}",
            location_text="Remote, Europe", work_mode="remote", closes_at=closes, closes_kind=kind,
            description="Python and PostgreSQL for satellite imagery. You will lead the team."))
    conn.commit()
    score_all(conn)

    everything = web.render("match", "", 0)
    assert '<span class="soon">apply by' in everything
    assert "ad ends" in everything and "deadline passed" in everything

    soon = web.render("match", "", 0, closes="2")
    assert "Engineering Manager" in soon and "Matches (1)" in soon and "closes=2" in soon
    assert "Staff Python Engineer" not in soon and "Python Engineering Lead" not in soon
    week = web.render("match", "", 0, closes="7")
    assert "Matches (2)" in week and "Head of Engineering" not in week
    assert "Matches (1)" in web.render("match", "", 0, closes="deadline")
    assert "Matches (4)" in web.render("match", "", 0, closes="bogus")

    by_closing = web.render("match", "", 0, sort="closing")
    order = [by_closing.index(t) for t in ("Engineering Manager", "Staff Python Engineer",
                                           "Head of Engineering")]
    assert order == sorted(order)


def test_applied_records_when_and_where(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    source = ensure_source(conn, "test", "Test board", "board")
    upsert_posting(conn, source, RawPosting(
        external_id="1", url="https://x.example/1", title="Engineering Manager",
        company_name="Orbit", location_text="Remote, Europe", work_mode="remote",
        description="Python and PostgreSQL for satellite imagery. You will lead the team."))
    conn.commit()
    score_all(conn)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]

    page = web.render("match", "", 0)
    assert f'id="apply-{job_id}"' in page and 'data-fresh="1"' in page
    assert f'onclick="openApply({job_id})"' in page

    web.save_review(job_id, "applied", datetime(2026, 10, 30, 2, 45), "~/Jobs/Orbit & Co")
    applied = web.render("applied", "", 0)
    assert "Applied 30 Oct 2026 02:45" in applied  # Warsaw time, as entered
    assert "<code>~/Jobs/Orbit &amp; Co</code>" in applied
    assert 'value="2026-10-30T02:45"' in applied and "data-fresh" not in applied
    stored = conn.execute("SELECT applied_at AT TIME ZONE 'UTC' FROM job_reviews").fetchone()[0]
    assert stored == datetime(2026, 10, 30, 1, 45)  # CET is UTC+1 after the clocks change

    # Another decision keeps the details; applying again without a time means now.
    web.save_review(job_id, "interested")
    assert conn.execute("SELECT application_dir FROM job_reviews").fetchone()[0] == "~/Jobs/Orbit & Co"
    web.save_review(job_id, "applied")
    assert "no folder given" in web.render("applied", "", 0)
    assert conn.execute("SELECT applied_at > now() - interval '1 minute' FROM job_reviews").fetchone()[0]


def test_last_checked_box(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    monkeypatch.setattr(web, "DISPLAY_TZ", "UTC")
    ok = ensure_source(conn, "ok", "Good board", "board")
    bad = ensure_source(conn, "bad", "Broken board", "board")
    ensure_source(conn, "new", "New board", "board")
    conn.execute("INSERT INTO collection_runs (source_id, started_at, status) VALUES "
                 "(%s, '2026-10-30 08:00+00', 'ok'), (%s, '2026-10-30 09:15+00', 'ok')", (ok, ok))
    conn.execute("INSERT INTO collection_runs (source_id, started_at, status, error) VALUES "
                 "(%s, now(), 'failed', 'timed out <x>')", (bad,))
    conn.commit()

    page = web.render("match", "", 0)
    box = page[page.index('<details class="checked">'):page.index("</details>")]
    assert re.search(r"<summary>Last checked \d\d:\d\d · <span class=\"failed\">1 failed", box)
    assert "<td>Good board</td><td>30 Oct 09:15</td>" in box  # latest run, with date as not today
    assert re.search(r"<td>Broken board</td><td>\d\d:\d\d</td>", box)  # today: time only
    assert 'class="failed" title="timed out &lt;x&gt;"' in box
    assert "<td>New board</td><td>never</td>" in box


def test_email_jobs_name_the_alert_sender(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    source = ensure_source(conn, "email_alerts", "Job-alert emails", "email")
    upsert_posting(conn, source, RawPosting(
        external_id="linkedin:1", url="https://www.linkedin.com/jobs/view/1/",
        title="Engineering Manager", company_name="Orbit & Co", location_text="Remote, Europe",
        work_mode="remote", description="Python and PostgreSQL for satellite imagery.",
        raw={"platform": "linkedin", "alert_from": "LinkedIn"}))
    conn.commit()
    score_all(conn)
    # The job list only: the "Last checked" box names the source itself.
    page = "".join(web.render(view, "", 0).split("<main>")[1] for view in web.VIEWS)
    assert "LinkedIn alert" in page and "Orbit &amp; Co" in page
    assert "Job-alert emails" not in page


def test_tracked_jobs_show_confirmation_and_missed_tab(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    board = ensure_source(conn, "test", "Test board", "board")
    email = ensure_source(conn, "email_alerts", "Job-alert emails", "email")
    ad = dict(company_name="Orbit", location_text="Remote, Europe", work_mode="remote",
              description="Python and PostgreSQL for satellite imagery. You will lead the team.")
    upsert_posting(conn, board, RawPosting(external_id="1", url="https://x.example/1",
                                           title="Engineering Manager", **ad))
    upsert_posting(conn, board, RawPosting(external_id="2", url="https://x.example/2",
                                           title="Director of Engineering", **ad))
    upsert_posting(conn, email, RawPosting(external_id="e3", url="https://x.example/3",
                                           title="Staff Python Engineer", **ad))
    conn.commit()
    score_all(conn)
    ids = dict(conn.execute("SELECT title, id FROM jobs").fetchall())

    page = web.render("match", "", 0)
    assert re.search(r"confirmed \d\d \w{3} \d\d:\d\d", page)
    assert "email alerts only, last seen" in page

    web.save_review(ids["Engineering Manager"], "interested")
    web.save_review(ids["Director of Engineering"], "applied")
    # The board stops listing both of its jobs.
    conn.execute("UPDATE postings SET last_seen_at = now() - interval '1 hour' WHERE source_id = %s",
                 (board,))
    close_unseen(conn, board, datetime.now(UTC))
    confirm_tracked_jobs(conn)
    conn.commit()

    page = web.render("missed", "", 0)
    assert "Missed (1)" in page and "Interested (0)" in page and "Applied (1)" in page
    main = page.split("<main>")[1]
    assert "Engineering Manager" in main and "advert gone" in main
    assert 'href="https://x.example/1"' in main  # still links to the last advert
    applied = web.render("applied", "", 0).split("<main>")[1]
    assert "Director of Engineering" in applied and "advert gone" in applied


def test_role_toggles_hide_staff_manager_and_director(conn, monkeypatch):  # noqa: F811
    monkeypatch.setitem(os.environ, "DATABASE_URL", DB_URL)
    source = ensure_source(conn, "test", "Test board", "board")
    for n, title in (("1", "Engineering Manager"), ("2", "Staff Python Engineer"),
                     ("3", "Head of Engineering"), ("4", "Senior Python Engineer")):
        upsert_posting(conn, source, RawPosting(
            external_id=n, url=f"https://x.example/{n}", title=title, company_name=f"Orbit {n}",
            location_text="Remote, Europe", work_mode="remote",
            description="Python and PostgreSQL for satellite imagery. You will lead the team."))
    conn.commit()
    score_all(conn)
    levels = dict(conn.execute(
        "SELECT j.title, s.role_level FROM jobs j JOIN job_scores s ON s.job_id = j.id").fetchall())
    assert levels["Engineering Manager"] == "manager" and levels["Head of Engineering"] == "director"
    assert levels["Staff Python Engineer"] == "staff" and levels["Senior Python Engineer"] == "senior"

    def shown(hide: str = "") -> set[str]:  # job titles listed on any tab
        lists = "".join(web.render(v, "", 0, hide=hide).split("<main>")[1] for v in web.VIEWS)
        return {t for t in levels if t in lists}

    # All on by default, each chip linking to the page with that level hidden.
    assert shown() == set(levels)
    page = web.render("match", "", 0)
    assert page.count('aria-checked="true"') == 3 and "hide=staff" in page

    assert shown("staff") == set(levels) - {"Staff Python Engineer"}
    page = web.render("match", "", 0, hide="staff,director,bogus", seen="24")
    assert "Staff Python Engineer" not in page and "Head of Engineering" not in page
    assert page.count('aria-checked="false"') == 2
    assert "hide=staff%2Cdirector" in page and "seen=24" in page  # tabs and links keep it
    assert 'name="hide" value="staff,director"' in page  # so does the filters form
    # The Staff chip turns staff back on and keeps director hidden.
    assert 'href="/?view=match&q=&seen=24&hide=director">Staff</a>' in page

    assert shown("staff,manager,director") == {"Senior Python Engineer"}  # other levels show
