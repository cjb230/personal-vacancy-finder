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
    assert parser_for("hello@mail.welcometothejungle.com").platform == "wttj"
    assert parser_for("help@welcometothejungle.com").platform == "wttj"
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


def test_real_linkedin_alert_layout():
    """An anonymised, trimmed copy of a real alert (October 2026)."""
    msg = to_stored((FIXTURES / "linkedin_job_alert_2026.eml").read_bytes(), 6)
    first, second = parse_message(msg)
    assert first.external_id == "linkedin:4484763155"
    assert first.url == "https://www.linkedin.com/jobs/view/4484763155/"
    assert (first.title, first.company_name, first.location_text, first.work_mode) == (
        "Field CTO - Poland", "CoberonChronos", "Warsaw Metropolitan Area (Remote)", "remote")
    assert (second.title, second.company_name, second.location_text, second.work_mode) == (
        "Chief Technology Officer", "heedly", "Barcelona (Hybrid)", "hybrid")


def test_linkedin_double_escaped_ampersand():
    from vacancy_finder.collectors.email_alerts import clean_text

    assert clean_text(" CTO &amp; Co-Founder ") == "CTO & Co-Founder"
    assert clean_text("HRS Hospitality &amp;amp; Retail") == "HRS Hospitality & Retail"


def test_remote_in_title_beats_onsite_location():
    from vacancy_finder.collectors.email_alerts import work_mode

    assert work_mode("Senior EM (100% Remote within Poland)", "Warsaw (On-site)") == "remote"
    assert work_mode("Remote Sensing Engineer", "Warsaw (On-site)") == "onsite"
    assert work_mode("Engineering Manager", "Poland (Remote)") == "remote"


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
    monkeypatch.setenv("IMAP_USER", "me@icloud.com")
    monkeypatch.setenv("IMAP_PASSWORD", "x")
    monkeypatch.delenv("IMAP_PARSED_FOLDER", raising=False)
    (collector,) = email_alerts.email_collectors()
    assert collector.settings.host == "imap.mail.me.com"
    assert collector.settings.parsed_folder == "Parsed job alerts"


def test_wttj_links():
    html = """<table>
      <tr><td><a href="https://www.welcometothejungle.com/en/companies/acme-space/jobs/engineering-manager_warsaw?utm_source=alert">Engineering Manager</a>
        <p>Acme Space</p><p>Warsaw, Poland</p></td></tr>
      <tr><td><a href="https://app.welcometothejungle.com/jobs/AbC123xy">Staff Engineer</a>
        <p>Beta Orbital</p><p>Remote</p></td></tr>
      <tr><td><a href="https://www.welcometothejungle.com/en/jobs?query=python">See more jobs</a></td></tr>
    </table>"""
    msg = email_alerts.StoredMessage("help@welcometothejungle.com", "New jobs", None, html, None)
    first, second = parse_message(msg)
    assert first.url == "https://www.welcometothejungle.com/en/companies/acme-space/jobs/engineering-manager_warsaw"
    assert (first.title, first.company_name, first.location_text) == ("Engineering Manager", "Acme Space", "Warsaw, Poland")
    assert second.url == "https://www.welcometothejungle.com/jobs/AbC123xy"
    assert second.company_name == "Beta Orbital"


class FakeImap:
    def __init__(self, capabilities, folders=()):
        self.caps = capabilities
        self.folders = set(folders)
        self.calls = []

    def capability(self):
        return "OK", [self.caps]

    def list(self, ref, pattern):
        return "OK", [b'(\\HasNoChildren) "/" ' + pattern.encode()] if pattern in self.folders else [None]

    def create(self, folder):
        self.calls.append(("CREATE", folder))
        self.folders.add(folder)
        return "OK", [b""]

    def subscribe(self, folder):
        self.calls.append(("SUBSCRIBE", folder))
        return "OK", [b""]

    def uid(self, *args):
        self.calls.append(args)
        return "OK", [b""]

    def expunge(self):
        self.calls.append(("EXPUNGE-ALL",))
        return "OK", [b""]


READ = ("STORE", "3,7", "+FLAGS.SILENT", r"(\Seen)")
FOLDER = '"Parsed job alerts"'


@pytest.mark.parametrize(
    ("caps", "expected"),
    [
        (b"IMAP4rev1 MOVE UIDPLUS", [READ, ("MOVE", "3,7", FOLDER)]),
        (b"IMAP4rev1 UIDPLUS", [
            READ,
            ("COPY", "3,7", FOLDER),
            ("STORE", "3,7", "+FLAGS.SILENT", r"(\Deleted)"),
            ("EXPUNGE", "3,7"),
        ]),
        (b"IMAP4rev1", [
            READ,
            ("COPY", "3,7", FOLDER),
            ("STORE", "3,7", "+FLAGS.SILENT", r"(\Deleted)"),
            ("EXPUNGE-ALL",),
        ]),
    ],
)
def test_parsed_are_marked_read_and_moved(caps, expected):
    inbox = email_alerts.Inbox(SETTINGS, writable=True)
    inbox.imap = FakeImap(caps, folders={FOLDER})
    inbox.file_parsed([3, 7])
    assert inbox.imap.calls == expected


def test_parsed_folder_created_when_missing():
    inbox = email_alerts.Inbox(SETTINGS, writable=True)
    inbox.imap = FakeImap(b"IMAP4rev1 MOVE")
    inbox.file_parsed([3, 7])
    assert inbox.imap.calls == [("CREATE", FOLDER), ("SUBSCRIBE", FOLDER), READ, ("MOVE", "3,7", FOLDER)]


def test_read_only_inbox_never_removes():
    inbox = email_alerts.Inbox(SETTINGS)
    inbox.imap = FakeImap(b"MOVE")
    with pytest.raises(RuntimeError):
        inbox.file_parsed([1])


from .test_store import DB_URL, conn, count  # noqa: E402,F401  (conn is a fixture)


class FakeInbox:
    """The alerts folder: uid -> (sender, message or None)."""

    def __init__(self, messages, uidvalidity=77):
        self.messages = messages
        self.uidvalidity = uidvalidity
        self.fetched = []
        self.removed = []

    def __call__(self, settings, writable=False):
        assert writable
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def uids(self, since=None):
        return sorted(self.messages)

    def senders(self, uids):
        return {uid: self.messages[uid][0] for uid in uids}

    def fetch(self, uid):
        self.fetched.append(uid)
        return self.messages[uid][1]

    def file_parsed(self, uids):
        self.removed.extend(uids)
        for uid in uids:
            del self.messages[uid]


def fresh_linkedin(uid):
    msg = linkedin_message(uid=uid)
    msg.received_at = datetime.now(UTC)
    return msg


@pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")
def test_stores_parses_and_removes_only_parsed(conn, monkeypatch):  # noqa: F811
    from vacancy_finder.runner import run_collector

    no_jobs = fresh_linkedin(6)
    no_jobs.body_html = "<p>Your application was viewed</p>"
    inbox = FakeInbox({
        5: ("jobalerts-noreply@linkedin.com", fresh_linkedin(5)),
        6: ("jobs-noreply@linkedin.com", no_jobs),
        9: ("friend@example.com", None),  # never downloaded
    })
    monkeypatch.setattr(email_alerts, "Inbox", inbox)

    run_collector(conn, EmailAlertsCollector(SETTINGS, days=30))
    assert inbox.fetched == [5, 6]
    assert inbox.removed == [5]
    assert sorted(inbox.messages) == [6, 9]
    assert count(conn, "SELECT count(*) FROM email_messages") == 2
    assert count(conn, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 2
    assert conn.execute("SELECT status FROM collection_runs ORDER BY id DESC LIMIT 1").fetchone() == ("ok",)

    # Next run: nothing downloaded again; the unparsed alert stays until a parser reads it.
    run_collector(conn, EmailAlertsCollector(SETTINGS, days=30))
    assert inbox.fetched == [5, 6]
    assert sorted(inbox.messages) == [6, 9]
    assert count(conn, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 2

    # Once the alerts are older than the window, their postings close.
    conn.execute("UPDATE email_messages SET received_at = now() - interval '40 days'")
    conn.commit()
    run_collector(conn, EmailAlertsCollector(SETTINGS, days=30))
    assert count(conn, "SELECT count(*) FROM postings WHERE closed_at IS NULL") == 0
