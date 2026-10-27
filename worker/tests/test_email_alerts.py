"""Email alerts: message decoding and the draft LinkedIn parser, plus storage against a database."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from vacancy_finder.collectors import email_alerts
from vacancy_finder.collectors.email_alerts import (
    EmailAlertsCollector,
    ImapSettings,
    parse_message,
    parser_for,
    to_stored,
)

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = ImapSettings("imap.example", 993, "me@example", "pw", "INBOX")


def linkedin_message(uid=1):
    return to_stored((FIXTURES / "linkedin_alert.eml").read_bytes(), uid)


def test_decodes_message():
    msg = linkedin_message()
    assert msg.from_address == "jobalerts-noreply@linkedin.com"
    assert msg.subject.startswith('"engineering manager"')
    assert msg.received_at == datetime(2026, 10, 27, 7, 12, tzinfo=UTC)
    assert "Staff Python Engineer" in msg.body_html
    assert "View job" in msg.body_text


def test_parser_chosen_by_sender_domain():
    assert parser_for("jobalerts-noreply@linkedin.com").platform == "linkedin"
    assert parser_for("hello@mail.welcometothejungle.com").platform == "otta"
    assert parser_for("alerts@nofluffjobs.com").platform == "nofluffjobs"
    assert parser_for("friend@example.com") is None


def test_linkedin_alert():
    first, second = parse_message(linkedin_message())
    assert first.external_id == "linkedin:4012345678"
    assert first.url == "https://www.linkedin.com/jobs/view/4012345678/"
    assert first.title == "Engineering Manager"
    assert first.company_name == "Acme Space"
    assert first.location_text == "Warsaw, Mazowieckie, Poland (Hybrid)"
    assert first.work_mode == "hybrid"
    assert first.raw["platform"] == "linkedin"
    assert (second.title, second.company_name) == ("Staff Python Engineer", "Beta Orbital")
    assert second.location_text == "European Union (Remote)"
    assert second.work_mode == "remote"


def test_unknown_sender_gives_no_postings():
    msg = linkedin_message()
    msg.from_address = "friend@example.com"
    assert parse_message(msg) == []


def test_login_alert_is_an_error():
    from vacancy_finder.collectors.email_alerts import check_login_alert

    check_login_alert([b"LOGIN completed"], {"CAPABILITY": [b"IMAP4rev1"]})
    with pytest.raises(RuntimeError, match="enable IMAP access"):
        check_login_alert([b"[ALERT] Please enable IMAP access in your account settings first."], {})


def test_off_without_credentials(monkeypatch):
    monkeypatch.delenv("IMAP_USER", raising=False)
    monkeypatch.delenv("IMAP_PASSWORD", raising=False)
    assert email_alerts.email_collectors() == []
    monkeypatch.setenv("IMAP_USER", "me@interia.pl")
    monkeypatch.setenv("IMAP_PASSWORD", "x")
    (collector,) = email_alerts.email_collectors()
    assert collector.settings.host == "poczta.interia.pl"


from .test_store import DB_URL, conn, count  # noqa: E402,F401  (conn is a fixture)


@pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")
def test_stores_new_messages_once_and_collects(conn, monkeypatch):  # noqa: F811
    from vacancy_finder.runner import run_collector

    calls = []

    def fake_fetch(settings, known_uids, since):
        calls.append(known_uids(77))
        msg = linkedin_message(uid=5)
        msg.received_at = datetime.now(UTC)
        return 77, [] if 5 in known_uids(77) else [msg]

    monkeypatch.setattr(email_alerts, "fetch_new", fake_fetch)
    run_collector(conn, EmailAlertsCollector(SETTINGS, days=30))
    run_collector(conn, EmailAlertsCollector(SETTINGS, days=30))
    assert calls == [set(), {5}]
    assert count(conn, "SELECT count(*) FROM email_messages") == 1
    assert count(conn, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 2
    assert conn.execute("SELECT status FROM collection_runs ORDER BY id DESC LIMIT 1").fetchone() == ("ok",)

    # Once the alert is older than the window, its postings close.
    conn.execute("UPDATE email_messages SET received_at = now() - interval '40 days'")
    conn.commit()
    run_collector(conn, EmailAlertsCollector(SETTINGS, days=30))
    assert count(conn, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 0
