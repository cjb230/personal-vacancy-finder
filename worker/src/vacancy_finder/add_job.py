"""Adding one vacancy by its URL, from the results page's Add job page (/add in web.py).

James pastes an advert's address. read() tries to read the advert, in this order:

1. LinkedIn and No Fluff Jobs pages are never fetched (LinkedIn's terms forbid it, and No
   Fluff Jobs' robots.txt bars every advert page). A LinkedIn job is keyed "linkedin:<id>",
   like its email alerts and captures, so they all end up on the same job.
2. A careers system the finder already reads (Greenhouse, Lever, Ashby, Workable, Personio,
   Teamtailor, Recruitee, Traffit, Comeet): the company's whole feed is read with its
   collector and the advert picked out of it.
3. A Just Join IT advert page, with that collector's page parser.
4. Any other page, if its robots.txt allows us: one fetch, read from the schema.org
   JobPosting block most careers sites include. The description is never guessed from the
   page's other text, since a wrong guess would be scored as if it were real.

Whatever is found fills in the form, which James checks or completes (pasting the description
when the page couldn't be read) before save() stores it. Saved adverts belong to the "manual"
source and go through upsert_posting, so they join an existing job with the same company and
title. For a careers system, save() can also start following the company's whole feed, as the
Employer link button does.
"""

import json
import re
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import psycopg

from . import employer
from .capture import MIN_LENGTH, clean_description, external_id_for, save_capture
from .collectors.ats import USER_AGENT, TrackedCompany, clean, from_iso, guess_work_mode, html_to_text
from .collectors.justjoinit import (
    OFFER_PREFIX,
    SitemapEntry,
    parse_offer_page,
    salary_from_job_posting,
)
from .discover import find_boards
from .models import RawPosting
from .normalize import normalize_company
from .store import ensure_source, posting_fingerprint, upsert_posting

SOURCE_KEY = "manual"
SOURCE_NAME = "Added by hand"
SOURCE_KIND = "manual"

WORK_MODES = ("remote", "hybrid", "onsite", "unknown")

# Sites we never fetch, and why. Adverts from them go straight to the paste box.
NEVER_FETCH = {
    "linkedin.com": "LinkedIn's terms forbid automated access, so its pages are never fetched.",
    "nofluffjobs.com": "No Fluff Jobs' robots.txt bars its advert pages, so they are never fetched.",
    "remotive.com": "Remotive's robots.txt and terms bar automated access, so it is never fetched.",
}

# Query parameters that only say where a click came from; dropped so the same advert reached
# from different places has one address.
_TRACKING = re.compile(r"^(utm_.*|ref|refid|referrer|source|src|trk|trackingid|gh_src|lever-source.*"
                       r"|lever-origin|fbclid|gclid|mc_[a-z]+|_hs[a-z]+)$", re.I)

# Fields of a read advert that the form doesn't show but that are kept for scoring (pay,
# contract, dates). They ride along in a hidden field between reading and saving.
EXTRA_FIELDS = ("contract_type", "salary_min", "salary_max", "salary_currency", "salary_period",
                "posted_at", "closes_at", "closes_kind")


@dataclass
class Draft:
    """What the Add job form is filled in with."""

    url: str
    external_id: str
    title: str = ""
    company: str = ""
    location: str = ""
    work_mode: str = "unknown"
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    ats: tuple[str, str] | None = None  # (ats_kind, ats_slug) when on a careers system we read
    read_from: str = ""  # where the details came from, e.g. "Greenhouse"
    problem: str = ""  # why the advert couldn't be read (fully), shown above the form


@dataclass
class Saved:
    job_id: int
    title: str
    joined: bool  # True when the advert joined a job that was already stored
    followed: str = ""  # what happened about following the company's careers feed


def canonical_url(url: str) -> str:
    """The advert's address without the fragment and click-tracking parameters."""
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not _TRACKING.match(k)])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))


def check_url(url: str) -> str:
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("That isn't a web address. Paste the advert's full URL, starting https://.")
    return url


def never_fetch(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    for domain, reason in NEVER_FETCH.items():
        if host == domain or host.endswith("." + domain):
            return reason
    return None


def read(url: str, conn: psycopg.Connection, client: httpx.Client) -> Draft:
    """Read what can be read about the advert at `url`. Never raises for an advert that can't
    be read: the draft's problem says why, and James pastes the rest. Raises ValueError only
    for something that isn't a web address."""
    url = check_url(url)
    linkedin = external_id_for(url)
    draft = Draft(url=canonical_url(url), external_id=linkedin or canonical_url(url))
    if linkedin:
        draft.url = f"https://www.linkedin.com/jobs/view/{linkedin.partition(':')[2]}/"
    if reason := never_fetch(url):
        draft.problem = reason + " Paste the job's details below."
        return draft
    boards = find_boards(url)
    try:
        if len(boards) == 1:
            draft.ats = boards.pop()
            _read_from_feed(draft, url, conn, client)
        elif url.startswith(OFFER_PREFIX):
            _read_justjoinit(draft, url, client)
        else:
            _read_page(draft, draft.url, client)  # without the click-tracking parameters
    except employer.READ_ERRORS as exc:
        draft.problem = f"The advert couldn't be read ({_short(exc)}). Paste the job's details below."
        return draft
    if not draft.problem and len(draft.description) < MIN_LENGTH:
        draft.problem = ("The description read from the page is too short to be the whole advert. "
                         "Paste the full text below." if draft.description else
                         "The page had no job description the finder could read. Paste it below.")
    return draft


def _short(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"the site answered {exc.response.status_code}"
    return str(exc) or type(exc).__name__


def _fill(draft: Draft, posting: RawPosting, read_from: str) -> None:
    draft.title = posting.title or ""
    if posting.company_name and normalize_company(posting.company_name) != normalize_company("(unknown)"):
        draft.company = posting.company_name
    draft.location = posting.location_text or ""
    draft.work_mode = posting.work_mode if posting.work_mode in WORK_MODES else "unknown"
    draft.description = clean_description(posting.description or "")
    draft.extra = {k: getattr(posting, k) for k in EXTRA_FIELDS if getattr(posting, k) is not None}
    draft.read_from = read_from


def company_on(conn: psycopg.Connection, kind: str, slug: str) -> tuple[int, str, bool] | None:
    """(id, name, tracked) of the company stored as having this careers feed, if any."""
    return conn.execute("SELECT id, name, tracked FROM companies WHERE ats_kind = %s AND ats_slug = %s",
                        (kind, slug)).fetchone()


def placeholder_name(slug: str) -> str:
    """A company name to start from when the feed's company isn't stored yet, e.g.
    "acme-space.jobs.personio.de" -> "Acme Space"."""
    word = slug.split("/")[0].split(".")[0]
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", word) if w) or slug


def _read_from_feed(draft: Draft, url: str, conn: psycopg.Connection, client: httpx.Client) -> None:
    kind, slug = draft.ats
    known = company_on(conn, kind, slug)
    name = known[1] if known else placeholder_name(slug)
    postings = employer.read_careers_page(client, kind, TrackedCompany(known[0] if known else 0, name, slug))
    draft.company = name
    source_name = employer.collector_for(kind).source_name
    found = pick_advert(postings, url)
    if found is None:
        draft.read_from = source_name
        draft.problem = (f"{name}'s careers page on {source_name} doesn't list this advert, so it may "
                         "have closed. Paste the job's details below if it is still open.")
        return
    _fill(draft, found, source_name)
    draft.company = name  # the feeds don't name the company; James can correct the guess


def pick_advert(postings: list[RawPosting], url: str) -> RawPosting | None:
    """The advert among a careers feed's that `url` points to: the same address, or the
    advert's own id appearing as a part of the address."""
    wanted = canonical_url(url).rstrip("/")
    for posting in postings:
        if posting.url and canonical_url(posting.url).rstrip("/") == wanted:
            return posting
    parts = set(re.split(r"[/?&=#.]+", urlsplit(url).path + "?" + urlsplit(url).query))
    for posting in postings:
        job_id = posting.external_id.rpartition(":")[2]
        job_id = job_id.rstrip("/").rpartition("/")[2]  # Teamtailor's ids are links
        if job_id and (job_id in parts or (len(job_id) >= 6 and job_id in url)):
            return posting
    return None


def _get(client: httpx.Client, url: str) -> str:
    response = client.get(url)
    response.raise_for_status()
    return response.text


def allowed_by_robots(client: httpx.Client, url: str) -> bool:
    """Whether the site's robots.txt lets USER_AGENT fetch `url`. A missing robots.txt allows
    everything; one we're refused (401/403) allows nothing, as the convention goes."""
    parts = urlsplit(url)
    try:
        response = client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
    except httpx.HTTPError:
        return True  # unreachable robots.txt: the page fetch will fail or succeed on its own
    if response.status_code in (401, 403):
        return False
    if response.status_code >= 400:
        return True
    rules = urllib.robotparser.RobotFileParser()
    rules.parse(response.text.splitlines())
    return rules.can_fetch(USER_AGENT, url)


def _read_justjoinit(draft: Draft, url: str, client: httpx.Client) -> None:
    entry = SitemapEntry(url.split("?")[0].split("#")[0], None)
    draft.url = draft.external_id = entry.url
    posting = parse_offer_page(_get(client, entry.url), entry)
    if posting is None:
        raise ValueError("no advert on the page")
    _fill(draft, posting, "Just Join IT")


def _read_page(draft: Draft, url: str, client: httpx.Client) -> None:
    if not allowed_by_robots(client, url):
        draft.problem = ("This site's robots.txt asks automated tools not to read this page, so it "
                         "wasn't fetched. Paste the job's details below.")
        return
    item = find_job_posting(_get(client, url))
    if item is None:
        draft.problem = ("The page has no job details the finder can read (no schema.org "
                         "JobPosting block). Paste them below.")
        return
    _fill(draft, posting_from_ld(item, draft.external_id, draft.url), urlsplit(url).hostname or "")


_LD_JSON = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def _is_job_posting(item: Any) -> bool:
    kind = item.get("@type") if isinstance(item, dict) else None
    return kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind)


def find_job_posting(page: str) -> dict[str, Any] | None:
    """The page's schema.org JobPosting, wherever it sits: alone, in a list or in an @graph."""
    for block in _LD_JSON.findall(page):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if _is_job_posting(item):
                    return item
                if "@graph" in item:
                    stack.append(item["@graph"])
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value if v)
    return clean(str(value)) if value else None


def _place(location: Any) -> str | None:
    places = []
    for item in location if isinstance(location, list) else [location]:
        if not isinstance(item, dict):
            continue
        address = item.get("address") or {}
        if isinstance(address, str):
            places.append(address)
            continue
        country = address.get("addressCountry")
        country = country.get("name") if isinstance(country, dict) else country
        place = ", ".join(p for p in (address.get("addressLocality"), country) if p)
        if place:
            places.append(place)
    return "; ".join(dict.fromkeys(places)) or None


def _salary_unit(unit: str) -> str | None:
    return {"hour": "hour", "day": "day", "month": "month", "year": "year"}.get(unit.lower())


def posting_from_ld(item: dict[str, Any], external_id: str, url: str) -> RawPosting:
    """A schema.org JobPosting as a posting."""
    location = _place(item.get("jobLocation"))
    remote = str(item.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    if remote and not location:
        location = _text(item.get("applicantLocationRequirements")) or "Remote"
    salary = salary_from_job_posting(item) or {}
    employment = item.get("employmentType")
    try:
        posted = from_iso(item.get("datePosted"))
    except ValueError:
        posted = None
    try:
        closes = from_iso(item.get("validThrough"))
    except ValueError:
        closes = None
    return RawPosting(
        external_id=external_id,
        url=url,
        title=_text(item.get("title")) or "",
        company_name=_text(item.get("hiringOrganization")) or "(unknown)",
        location_text=location,
        work_mode="remote" if remote else guess_work_mode(location, item.get("title")),
        description=html_to_text(item.get("description")),
        contract_type=_text(employment),
        salary_min=_decimal(salary.get("from")),
        salary_max=_decimal(salary.get("to")),
        salary_currency=salary.get("currency"),
        salary_period=_salary_unit(salary.get("unit") or ""),
        posted_at=posted,
        closes_at=closes,
        closes_kind="expiry" if closes else None,
    )


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


def extra_to_json(extra: dict[str, Any]) -> str:
    return json.dumps({k: (v.isoformat() if isinstance(v, datetime) else str(v))
                       for k, v in extra.items() if k in EXTRA_FIELDS})


def extra_from_json(text: str) -> dict[str, Any]:
    """The hidden field back as posting fields, keeping only well-formed values."""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return {}
    extra: dict[str, Any] = {}
    for key, value in (data.items() if isinstance(data, dict) else []):
        if key not in EXTRA_FIELDS or not isinstance(value, str):
            continue
        if key.startswith("salary_m"):
            if (number := _decimal(value)) is not None:
                extra[key] = number
        elif key.endswith("_at"):
            try:
                extra[key] = datetime.fromisoformat(value)
            except ValueError:
                pass
        elif key == "closes_kind":
            if value in ("deadline", "expiry"):
                extra[key] = value
        elif key == "salary_period":
            if value in ("hour", "day", "month", "year"):
                extra[key] = value
        else:
            extra[key] = value[:200]
    if "closes_at" not in extra:
        extra.pop("closes_kind", None)
    return extra


def save(conn: psycopg.Connection, draft: Draft, follow: bool = False,
         client: httpx.Client | None = None) -> Saved:
    """Store the advert as James completed it, and with follow (on a careers system we read)
    start following the company's careers feed. Commits; the caller rescores. Raises
    ValueError for a form that is missing the title, company or description."""
    title, company = draft.title.strip()[:500], draft.company.strip()[:500]
    description = clean_description(draft.description)
    if not title or not company:
        raise ValueError("The job needs a title and a company.")
    if normalize_company(company) in ("", normalize_company("(unknown)")):
        raise ValueError("Give the company's name, so the job can be matched with other listings of it.")
    if len(description) < MIN_LENGTH:
        raise ValueError("That is too short to be a job description. Paste the advert's full text.")
    posting = RawPosting(
        external_id=draft.external_id or canonical_url(draft.url), url=draft.url, title=title,
        company_name=company, location_text=draft.location.strip()[:500] or None,
        work_mode=draft.work_mode if draft.work_mode in WORK_MODES else "unknown",
        description=description, raw={"added_by_hand": True, "read_from": draft.read_from},
        **draft.extra)
    source_id = ensure_source(conn, SOURCE_KEY, SOURCE_NAME, SOURCE_KIND)
    joined = conn.execute(
        """
        SELECT EXISTS (SELECT 1 FROM jobs j WHERE j.fingerprint = %s AND EXISTS (
            SELECT 1 FROM postings p WHERE p.job_id = j.id
              AND NOT (p.source_id = %s AND p.external_id = %s)))
        """, (posting_fingerprint(source_id, posting), source_id, posting.external_id)).fetchone()[0]
    upsert_posting(conn, source_id, posting)
    if posting.external_id.startswith("linkedin:"):
        # So the LinkedIn alert postings get the text too, as with the Capture bookmarklet.
        save_capture(conn, draft.url, description, title, company)
    job_id = conn.execute("SELECT job_id FROM postings WHERE source_id = %s AND external_id = %s",
                          (source_id, posting.external_id)).fetchone()[0]
    conn.commit()
    followed = ""
    if follow and draft.ats:
        followed = _follow(conn, draft.ats, job_id, client)
    return Saved(job_id, title, joined, followed)


def _follow(conn: psycopg.Connection, ats: tuple[str, str], job_id: int,
            client: httpx.Client | None) -> str:
    kind, slug = ats
    source_name = employer.collector_for(kind).source_name
    company_id, name = conn.execute(
        "SELECT c.id, c.name FROM jobs j JOIN companies c ON c.id = j.company_id WHERE j.id = %s",
        (job_id,)).fetchone()
    known = company_on(conn, kind, slug)
    if known and known[0] != company_id:
        return (f"Its careers page on {source_name} is already stored for {known[1]}, so it wasn't "
                f"followed again for {name}.")
    if known and known[2]:
        return f"{name}'s careers page on {source_name} was already followed."
    company = TrackedCompany(company_id, name, slug)
    own_client = client is None
    client = client or employer.client()
    try:
        postings = employer.read_careers_page(client, kind, company)
    except employer.READ_ERRORS as exc:
        employer.follow_careers_page(conn, company, kind, [])  # the next run tries again
        return (f"{name} is now followed on {source_name}, but its careers page couldn't be read "
                f"just now ({_short(exc)}). It will be tried again on the next careers-page run.")
    finally:
        if own_client:
            client.close()
    employer.follow_careers_page(conn, company, kind, postings)
    return f"{name} is now followed on {source_name}: {len(postings)} adverts collected."
