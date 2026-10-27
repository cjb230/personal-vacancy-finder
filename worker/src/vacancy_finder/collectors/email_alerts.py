"""Job-alert emails from sites we can't collect from directly: LinkedIn, Otta (Welcome to
the Jungle) and No Fluff Jobs.

The alerts inbox is read over IMAP in read-only mode, so nothing is marked read or moved.
Each new message is stored in email_messages; then every alert from the last
EMAIL_ALERT_DAYS is parsed again, so parser fixes apply to old alerts too. A posting
stays open while its newest alert is inside that window and closes after.

Settings: IMAP_USER and IMAP_PASSWORD (required, or the collector is off), IMAP_HOST
(default poczta.interia.pl), IMAP_PORT (993), IMAP_FOLDER (INBOX), EMAIL_ALERT_DAYS (30).

The parsers are first drafts until fitted to real alerts: they find job links by URL and
read the title and the company / location lines around each link.
"""

import email
import email.policy
import imaplib
import logging
import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime

import psycopg

from ..models import RawPosting
from .ats import guess_work_mode, html_to_text
from .page import JobLink, find_job_links

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImapSettings:
    host: str
    port: int
    user: str
    password: str
    folder: str

    @property
    def mailbox(self) -> str:
        return f"{self.user}@{self.host}/{self.folder}"


def imap_settings_from_env() -> ImapSettings | None:
    user, password = os.environ.get("IMAP_USER"), os.environ.get("IMAP_PASSWORD")
    if not (user and password):
        return None
    return ImapSettings(
        host=os.environ.get("IMAP_HOST") or "poczta.interia.pl",
        port=int(os.environ.get("IMAP_PORT") or "993"),
        user=user,
        password=password,
        folder=os.environ.get("IMAP_FOLDER") or "INBOX",
    )


@dataclass
class StoredMessage:
    from_address: str | None
    subject: str | None
    received_at: datetime | None
    body_html: str | None
    body_text: str | None
    message_id: str | None = None
    uid: int | None = None
    raw: bytes | None = None


def to_stored(raw: bytes, uid: int | None = None) -> StoredMessage:
    msg: EmailMessage = email.message_from_bytes(raw, policy=email.policy.default)
    html_part = msg.get_body(("html",))
    text_part = msg.get_body(("plain",))
    try:
        received = parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
    except (TypeError, ValueError):
        received = None
    return StoredMessage(
        from_address=parseaddr(str(msg["From"] or ""))[1].lower() or None,
        subject=str(msg["Subject"] or "") or None,
        received_at=received,
        body_html=html_part.get_content() if html_part else None,
        body_text=text_part.get_content() if text_part else None,
        message_id=str(msg["Message-ID"] or "") or None,
        uid=uid,
        raw=raw,
    )


def check_login_alert(login_data: list, untagged: dict) -> None:
    """Some servers accept the login but refuse everything after it with an [ALERT], e.g.
    Interia until IMAP access is switched on in its settings. Fail clearly instead of hanging."""
    lines = [*login_data, *(line for values in untagged.values() for line in values)]
    for line in lines:
        text = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        if "[ALERT]" in text.upper():
            raise RuntimeError(f"IMAP server refused access after login: {text}")


def fetch_new(settings: ImapSettings, known_uids: Callable[[int], set[int]], since: datetime,
              limit: int | None = None) -> tuple[int, list[StoredMessage]]:
    """Messages since `since` whose UIDs aren't known yet, at most the newest `limit`.
    Returns (uidvalidity, messages)."""
    with imaplib.IMAP4_SSL(settings.host, settings.port, timeout=60) as imap:
        _, data = imap.login(settings.user, settings.password)
        check_login_alert(data, imap.untagged_responses)
        status, _ = imap.select(settings.folder, readonly=True)
        if status != "OK":
            raise RuntimeError(f"cannot open folder {settings.folder!r}")
        uidvalidity = int(re.search(rb"\d+", imap.response("UIDVALIDITY")[1][0]).group())
        _, data = imap.uid("SEARCH", None, "SINCE", since.strftime("%d-%b-%Y"))
        uids = [int(u) for u in data[0].split()] if data and data[0] else []
        known = known_uids(uidvalidity)
        uids = [uid for uid in uids if uid not in known]
        if limit:
            uids = uids[-limit:]
        messages = []
        for uid in uids:
            _, parts = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")  # PEEK leaves it unread
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if raw:
                messages.append(to_stored(raw, uid))
        return uidvalidity, messages


# --- parsers, one per alert sender ---------------------------------------------------------


@dataclass(frozen=True)
class AlertParser:
    platform: str
    sender: re.Pattern  # matched against the From address
    link: re.Pattern  # job links; group 1 is the platform's job id
    canonical: str  # job URL from the id, without tracking parameters

    def postings(self, msg: StoredMessage) -> Iterator[RawPosting]:
        if not msg.body_html:
            return
        for job in find_job_links(msg.body_html, "https://example.invalid/", self.link,
                                  key=lambda href: self.link.search(href).group(1)):
            job_id = self.link.search(job.url).group(1)
            company, location = company_and_location(job)
            yield RawPosting(
                external_id=f"{self.platform}:{job_id}",
                url=self.canonical.format(id=job_id),
                title=job.title or "(untitled)",
                company_name=company or "(unknown company)",
                location_text=location,
                work_mode=guess_work_mode(location),
                posted_at=msg.received_at,
                raw={"platform": self.platform, "subject": msg.subject,
                     "card_text": card_lines(job)[:12]},
            )


def card_lines(job: JobLink) -> list[str]:
    lines = [" ".join(line.split()) for line in job.card.get_text("\n").split("\n")]
    return [line for line in lines if line]


_NOISE = re.compile(r"^(apply|view|see|easy apply|new|promoted|actively recruiting|\d+ (applicants?|connections?)|be an early applicant)\b", re.I)


def company_and_location(job: JobLink) -> tuple[str | None, str | None]:
    """The lines after the title in the job's card: usually company, then location.
    LinkedIn puts both on one line as "Company · Location"."""
    lines = card_lines(job)
    if job.title in lines:
        lines = lines[lines.index(job.title) + 1:]
    lines = [line for line in lines if not _NOISE.match(line)]
    if not lines:
        return None, job.location
    first = re.split(r"\s+[·•|]\s+", lines[0], maxsplit=1)
    if len(first) == 2:
        return first[0], job.location or first[1]
    return first[0], job.location or (lines[1] if len(lines) > 1 else None)


PARSERS = [
    AlertParser("linkedin", re.compile(r"linkedin\.com$"),
                re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)"),
                "https://www.linkedin.com/jobs/view/{id}/"),
    AlertParser("otta", re.compile(r"(otta\.com|welcometothejungle\.com)$"),
                re.compile(r"(?:otta\.com|welcometothejungle\.com)/jobs/([A-Za-z0-9_-]+)"),
                "https://app.welcometothejungle.com/jobs/{id}"),
    AlertParser("nofluffjobs", re.compile(r"nofluffjobs\.com$"),
                re.compile(r"nofluffjobs\.com/(?:[a-z]{2}/)?job/([a-z0-9-]+)"),
                "https://nofluffjobs.com/job/{id}"),
]


def parser_for(from_address: str | None) -> AlertParser | None:
    domain = (from_address or "").rpartition("@")[2]
    return next((p for p in PARSERS if p.sender.search(domain)), None)


def parse_message(msg: StoredMessage) -> list[RawPosting]:
    parser = parser_for(msg.from_address)
    if parser is None:
        return []
    postings = list(parser.postings(msg))
    if not postings and msg.body_text:
        log.info("%s alert %r has no job links in its HTML", parser.platform, msg.subject)
    return postings


# --- collector -----------------------------------------------------------------------------


class EmailAlertsCollector:
    source_key = "email_alerts"
    source_name = "Job-alert emails (LinkedIn, Otta, No Fluff Jobs)"
    source_kind = "email"

    def __init__(self, settings: ImapSettings, days: int | None = None) -> None:
        self.settings = settings
        self.days = days or int(os.environ.get("EMAIL_ALERT_DAYS", "30"))

    def incomplete_company_ids(self) -> set[int]:
        return set()

    def collect(self, conn: psycopg.Connection) -> Iterator[RawPosting]:
        since = datetime.now(UTC) - timedelta(days=self.days)
        mailbox = self.settings.mailbox

        def known_uids(uidvalidity: int) -> set[int]:
            rows = conn.execute(
                "SELECT uid FROM email_messages WHERE mailbox = %s AND uidvalidity = %s",
                (mailbox, uidvalidity),
            ).fetchall()
            return {uid for (uid,) in rows}

        uidvalidity, new = fetch_new(self.settings, known_uids, since)
        for m in new:
            conn.execute(
                """
                INSERT INTO email_messages (mailbox, uidvalidity, uid, message_id, from_address,
                                            subject, received_at, body_html, body_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (mailbox, uidvalidity, m.uid, m.message_id, m.from_address, m.subject,
                 m.received_at, m.body_html, m.body_text),
            )
        conn.commit()
        log.info("email_alerts: %d new messages", len(new))

        # Oldest first, so each posting ends up with the details of its latest mention.
        rows = conn.execute(
            """
            SELECT from_address, subject, received_at, body_html, body_text FROM email_messages
            WHERE mailbox = %s AND received_at >= %s ORDER BY received_at
            """,
            (mailbox, since),
        ).fetchall()
        for row in rows:
            yield from parse_message(StoredMessage(*row))


def email_collectors() -> list[EmailAlertsCollector]:
    settings = imap_settings_from_env()
    return [EmailAlertsCollector(settings)] if settings else []
