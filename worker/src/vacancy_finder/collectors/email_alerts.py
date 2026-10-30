"""Job-alert emails from sites we can't collect from directly: LinkedIn, Welcome to the
Jungle (Otta) and No Fluff Jobs.

Each run stores every new alert (from a sender with a parser) in email_messages, then re-parses the
alerts of the last EMAIL_ALERT_DAYS, so parser fixes apply to old alerts too. A posting stays
open while its newest alert is inside that window and closes after.

Messages that gave at least one posting are moved out of the inbox to IMAP_TRASH_FOLDER (or
deleted outright if that is empty); the stored copy stays in the database. Anything else
stays in the inbox, unread, so what is left there shows where the parsers fall short.

Settings: IMAP_USER and IMAP_PASSWORD (required, or the collector is off), IMAP_HOST
(default imap.mail.me.com, iCloud), IMAP_PORT (993), IMAP_FOLDER (INBOX), IMAP_TRASH_FOLDER
("Deleted Messages", iCloud's bin), EMAIL_ALERT_DAYS (30).

The parsers find job links by URL and read the title and the company / location lines
around each link.
"""

import email
import email.policy
import html
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
    trash_folder: str = "Deleted Messages"  # empty: delete outright

    @property
    def mailbox(self) -> str:
        return f"{self.user}@{self.host}/{self.folder}"


def imap_settings_from_env() -> ImapSettings | None:
    user, password = os.environ.get("IMAP_USER"), os.environ.get("IMAP_PASSWORD")
    if not (user and password):
        return None
    return ImapSettings(
        host=os.environ.get("IMAP_HOST") or "imap.mail.me.com",
        port=int(os.environ.get("IMAP_PORT") or "993"),
        user=user,
        password=password,
        folder=os.environ.get("IMAP_FOLDER") or "INBOX",
        trash_folder=os.environ.get("IMAP_TRASH_FOLDER", "Deleted Messages"),
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


class Inbox:
    """One IMAP session on the alerts folder. Opened read-only unless `writable`."""

    def __init__(self, settings: ImapSettings, writable: bool = False) -> None:
        self.settings = settings
        self.writable = writable

    def __enter__(self) -> "Inbox":
        self.imap = imaplib.IMAP4_SSL(self.settings.host, self.settings.port, timeout=60)
        try:
            _, data = self.imap.login(self.settings.user, self.settings.password)
            check_login_alert(data, self.imap.untagged_responses)
            status, _ = self.imap.select(_quote(self.settings.folder), readonly=not self.writable)
            if status != "OK":
                raise RuntimeError(f"cannot open folder {self.settings.folder!r}")
            self.uidvalidity = int(
                re.search(rb"\d+", self.imap.response("UIDVALIDITY")[1][0]).group()
            )
        except BaseException:
            self.imap.shutdown()
            raise
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass

    def uids(self, since: datetime | None = None) -> list[int]:
        criteria = ("SINCE", since.strftime("%d-%b-%Y")) if since else ("ALL",)
        _, data = self.imap.uid("SEARCH", None, *criteria)
        return [int(u) for u in data[0].split()] if data and data[0] else []

    def senders(self, uids: list[int]) -> dict[int, str | None]:
        """The From address of each message, without downloading the rest."""
        if not uids:
            return {}
        _, parts = self.imap.uid(
            "FETCH", ",".join(map(str, uids)), "(UID BODY.PEEK[HEADER.FIELDS (FROM)])"
        )
        found = {}
        for part in parts:
            if isinstance(part, tuple) and (m := re.search(rb"UID (\d+)", part[0])):
                header = email.message_from_bytes(part[1], policy=email.policy.default)
                found[int(m.group(1))] = parseaddr(str(header["From"] or ""))[1].lower() or None
        return found

    def fetch(self, uid: int) -> StoredMessage | None:
        _, parts = self.imap.uid("FETCH", str(uid), "(BODY.PEEK[])")  # PEEK leaves it unread
        raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
        return to_stored(raw, uid) if raw else None

    def remove(self, uids: list[int]) -> None:
        """Moves the messages to the trash folder, or deletes them if there is none."""
        if not uids:
            return
        if not self.writable:
            raise RuntimeError("inbox was opened read-only")
        uid_set = ",".join(map(str, uids))
        trash = self.settings.trash_folder
        if trash and b"MOVE" in self._capabilities():
            status, data = self.imap.uid("MOVE", uid_set, _quote(trash))
            if status != "OK":
                raise RuntimeError(f"cannot move messages to {trash!r}: {data}")
            return
        if trash:
            status, data = self.imap.uid("COPY", uid_set, _quote(trash))
            if status != "OK":
                raise RuntimeError(f"cannot copy messages to {trash!r}: {data}")
        self.imap.uid("STORE", uid_set, "+FLAGS.SILENT", r"(\Deleted)")
        if b"UIDPLUS" in self._capabilities():
            self.imap.uid("EXPUNGE", uid_set)  # only these, not others marked deleted
        else:
            self.imap.expunge()

    def _capabilities(self) -> set[bytes]:
        _, data = self.imap.capability()
        return set(data[0].upper().split()) if data and data[0] else set()


def _quote(folder: str) -> str:
    return f'"{folder}"' if " " in folder and not folder.startswith('"') else folder


# --- parsers, one per alert sender ---------------------------------------------------------


@dataclass(frozen=True)
class AlertParser:
    platform: str
    sender: re.Pattern  # matched against the From address
    link: re.Pattern  # job links; group 1 is the platform's job id
    canonical: str  # job URL from the id, without tracking parameters
    # (title, company, location) from a job's card, for senders whose layout we know;
    # None for any part it can't find, which then falls back to the generic guess.
    card_fields: Callable[[JobLink], tuple[str | None, str | None, str | None]] | None = None

    def postings(self, msg: StoredMessage) -> Iterator[RawPosting]:
        if not msg.body_html:
            return
        for job in find_job_links(msg.body_html, "https://example.invalid/", self.link,
                                  key=lambda href: self.link.search(href).group(1)):
            job_id = self.link.search(job.url).group(1)
            title, company, location = (
                self.card_fields(job) if self.card_fields else (None, None, None)
            )
            if not (title and company):
                company, location = company_and_location(job)
                title = job.title
            yield RawPosting(
                external_id=f"{self.platform}:{job_id}",
                url=self.canonical.format(id=job_id),
                title=title or "(untitled)",
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


def _has_class(tag, word: str) -> bool:
    return word in (tag.get("class") or [])


def linkedin_card(job: JobLink) -> tuple[str | None, str | None, str | None]:
    """LinkedIn job cards: the title is a bold link, and the line under it reads
    "Company · Location (Remote)". Badges such as "Easy Apply" sit in separate labels."""
    title_tag = job.card.find(lambda t: t.name == "a" and _has_class(t, "font-bold"))
    line_tag = job.card.find(lambda t: t.name == "p" and _has_class(t, "text-system-gray-100"))
    title = clean_text(title_tag.get_text(" ")) if title_tag else None
    line = clean_text(line_tag.get_text(" ")) if line_tag else None
    if not line:
        return title, None, None
    company, _, location = line.partition(" · ")
    return title, company or None, location or None


def clean_text(text: str) -> str | None:
    # LinkedIn escapes some text more than once ("&amp;amp;..."), so unescape until stable.
    for _ in range(3):
        if (unescaped := html.unescape(text)) == text:
            break
        text = unescaped
    return " ".join(text.split()) or None


PARSERS = [
    AlertParser("linkedin", re.compile(r"linkedin\.com$"),
                re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)"),
                "https://www.linkedin.com/jobs/view/{id}/", linkedin_card),
    # Welcome to the Jungle links are either app.welcometothejungle.com/jobs/{id} (the old Otta
    # app) or www.welcometothejungle.com/{lang}/companies/{company}/jobs/{slug}.
    AlertParser("wttj", re.compile(r"(otta\.com|welcometothejungle\.com)$"),
                re.compile(r"(?:otta\.com|welcometothejungle\.com)/"
                           r"((?:[a-z]{2}/)?(?:companies/[\w-]+/)?jobs/[\w-]+)"),
                "https://www.welcometothejungle.com/{id}"),
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
    source_name = "Job-alert emails (LinkedIn, Welcome to the Jungle, No Fluff Jobs)"
    source_kind = "email"

    def __init__(self, settings: ImapSettings, days: int | None = None) -> None:
        self.settings = settings
        self.days = days or int(os.environ.get("EMAIL_ALERT_DAYS", "30"))

    def incomplete_company_ids(self) -> set[int]:
        return set()

    def collect(self, conn: psycopg.Connection) -> Iterator[RawPosting]:
        since = datetime.now(UTC) - timedelta(days=self.days)
        mailbox = self.settings.mailbox
        with Inbox(self.settings, writable=True) as inbox:
            stored = {
                uid: StoredMessage(*rest)
                for uid, *rest in conn.execute(
                    """
                    SELECT uid, from_address, subject, received_at, body_html, body_text
                    FROM email_messages WHERE mailbox = %s AND uidvalidity = %s
                    """,
                    (mailbox, inbox.uidvalidity),
                )
            }
            # Only alerts from senders we have a parser for are downloaded; the rest of the
            # inbox is never read.
            unseen = [uid for uid in inbox.uids(since) if uid not in stored]
            new = []
            for uid, sender in inbox.senders(unseen).items():
                if parser_for(sender) and (m := inbox.fetch(uid)):
                    new.append(m)
                    stored[uid] = m
            for m in new:
                conn.execute(
                    """
                    INSERT INTO email_messages (mailbox, uidvalidity, uid, message_id,
                        from_address, subject, received_at, body_html, body_text)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (mailbox, inbox.uidvalidity, m.uid, m.message_id, m.from_address,
                     m.subject, m.received_at, m.body_html, m.body_text),
                )
            conn.commit()  # stored before anything leaves the server

            # Everything still in the inbox that now parses goes, including older messages
            # that an improved parser has learned to read.
            parsed = [uid for uid in inbox.uids() if uid in stored and parse_message(stored[uid])]
            inbox.remove(parsed)
            log.info("email_alerts: %d new messages, %d parsed and removed from %s",
                     len(new), len(parsed), self.settings.folder)

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
