"""Collectors for public careers feeds of applicant tracking systems (ATS).

Each one polls every tracked company whose ats_kind matches, using that
company's slug on the ATS, e.g. boards-api.greenhouse.io/v1/boards/{slug}/jobs.
"""

import html
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import psycopg

from ..models import RawPosting

log = logging.getLogger(__name__)

USER_AGENT = "personal-vacancy-finder/0.1 (personal job search)"


@dataclass(frozen=True)
class TrackedCompany:
    id: int
    name: str
    slug: str


def tracked_companies(conn: psycopg.Connection, ats_kind: str) -> list[TrackedCompany]:
    rows = conn.execute(
        """
        SELECT id, name, ats_slug FROM companies
        WHERE tracked AND NOT excluded AND ats_kind = %s AND ats_slug IS NOT NULL
        ORDER BY name
        """,
        (ats_kind,),
    ).fetchall()
    return [TrackedCompany(*row) for row in rows]


def html_to_text(fragment: str | None) -> str | None:
    if not fragment:
        return None
    text = html.unescape(fragment)  # Greenhouse double-escapes its HTML
    text = re.sub(r"<(br|/p|/li|/h\d)\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def clean(value: str | None) -> str | None:
    """Some feeds HTML-escape plain fields, e.g. "R&amp;D" in a title."""
    if value is None:
        return None
    # Unescape until stable: some sources escape twice ("&amp;amp;").
    for _ in range(3):
        unescaped = html.unescape(value)
        if unescaped == value:
            break
        value = unescaped
    return value.strip() or None


def guess_work_mode(*texts: str | None) -> str:
    """Fallback for feeds without a structured field: look for the words in location text."""
    joined = " ".join(t for t in texts if t).lower()
    if "hybrid" in joined:
        return "hybrid"
    if "remote" in joined:
        return "remote"
    return "unknown"


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def from_millis(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value / 1000, UTC) if value else None


class AtsCollector:
    """Shared loop: fetch each tracked company's feed, parse it, note which ones failed."""

    source_kind = "ats"
    source_key: str
    source_name: str

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client
        self._incomplete: set[int] = set()

    def incomplete_company_ids(self) -> set[int]:
        return self._incomplete

    def collect(self, conn: psycopg.Connection) -> Iterator[RawPosting]:
        self._incomplete = set()
        companies = tracked_companies(conn, self.source_key)
        client = self._client or httpx.Client(
            timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        try:
            for company in companies:
                try:
                    payload = self.fetch(client, company.slug)
                    postings = list(self.parse(payload, company))
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    log.warning("%s: %s (%s) failed: %r", self.source_key, company.name, company.slug, exc)
                    self._incomplete.add(company.id)
                    continue
                log.debug("%s: %s has %d postings", self.source_key, company.name, len(postings))
                yield from postings
        finally:
            if self._client is None:
                client.close()
        if companies and len(self._incomplete) == len(companies):
            raise RuntimeError(f"every {self.source_key} company failed")

    def fetch(self, client: httpx.Client, slug: str) -> Any:
        response = client.get(self.feed_url(slug))
        response.raise_for_status()
        return response.json()

    def feed_url(self, slug: str) -> str:
        raise NotImplementedError

    def parse(self, payload: Any, company: TrackedCompany) -> Iterable[RawPosting]:
        raise NotImplementedError


class GreenhouseCollector(AtsCollector):
    source_key = "greenhouse"
    source_name = "Greenhouse job boards"

    def feed_url(self, slug: str) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

    def parse(self, payload: Any, company: TrackedCompany) -> Iterable[RawPosting]:
        for job in payload["jobs"]:
            location = (job.get("location") or {}).get("name")
            yield RawPosting(
                external_id=f"{company.slug}:{job['id']}",
                url=job["absolute_url"],
                title=clean(job["title"]) or "(untitled)",
                company_name=company.name,
                location_text=clean(location),
                work_mode=guess_work_mode(location, job["title"]),
                description=html_to_text(job.get("content")),
                posted_at=from_iso(job.get("first_published") or job.get("updated_at")),
                raw=job,
                ats_kind=self.source_key,
                ats_slug=company.slug,
            )


class LeverCollector(AtsCollector):
    source_key = "lever"
    source_name = "Lever job sites"
    host = "api.lever.co"

    _WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite"}
    _INTERVALS = {"per-hour-wage": "hour", "per-day-wage": "day", "per-month-salary": "month",
                  "per-year-salary": "year"}

    def feed_url(self, slug: str) -> str:
        return f"https://{self.host}/v0/postings/{slug}?mode=json"

    def parse(self, payload: Any, company: TrackedCompany) -> Iterable[RawPosting]:
        for job in payload:
            categories = job.get("categories") or {}
            location = categories.get("location")
            all_locations = categories.get("allLocations") or []
            if len(all_locations) > 1:
                location = "; ".join(all_locations)
            salary = job.get("salaryRange") or {}
            yield RawPosting(
                external_id=f"{company.slug}:{job['id']}",
                url=job["hostedUrl"],
                title=clean(job["text"]) or "(untitled)",
                company_name=company.name,
                location_text=clean(location),
                work_mode=self._WORKPLACE.get(job.get("workplaceType") or "")
                or guess_work_mode(location, job["text"]),
                description=job.get("descriptionPlain") or html_to_text(job.get("description")),
                contract_type=categories.get("commitment"),
                salary_min=_decimal(salary.get("min")),
                salary_max=_decimal(salary.get("max")),
                salary_currency=salary.get("currency"),
                salary_period=self._INTERVALS.get(salary.get("interval") or ""),
                posted_at=from_millis(job.get("createdAt")),
                raw=job,
                ats_kind=self.source_key,
                ats_slug=company.slug,
            )


class LeverEuCollector(LeverCollector):
    """Lever's EU-hosted instance, used by some European companies."""

    source_key = "lever-eu"
    source_name = "Lever job sites (EU)"
    host = "api.eu.lever.co"


class AshbyCollector(AtsCollector):
    source_key = "ashby"
    source_name = "Ashby job boards"

    _WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}

    def feed_url(self, slug: str) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"

    def parse(self, payload: Any, company: TrackedCompany) -> Iterable[RawPosting]:
        for job in payload["jobs"]:
            if job.get("isListed") is False:
                continue
            locations = [job.get("location")] + [
                loc.get("location") for loc in job.get("secondaryLocations") or []
            ]
            location = "; ".join(loc for loc in locations if loc) or None
            workplace = (job.get("workplaceType") or "").lower().replace("-", "")
            work_mode = self._WORKPLACE.get(workplace) or (
                "remote" if job.get("isRemote") else guess_work_mode(location)
            )
            yield RawPosting(
                external_id=f"{company.slug}:{job['id']}",
                url=job.get("jobUrl") or job["applyUrl"],
                title=clean(job["title"]) or "(untitled)",
                company_name=company.name,
                location_text=clean(location),
                work_mode=work_mode,
                description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml")),
                contract_type=job.get("employmentType"),
                posted_at=from_iso(job.get("publishedAt")),
                raw=job,
                ats_kind=self.source_key,
                ats_slug=company.slug,
            )


class WorkableCollector(AtsCollector):
    source_key = "workable"
    source_name = "Workable careers pages"

    def feed_url(self, slug: str) -> str:
        return f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"

    def parse(self, payload: Any, company: TrackedCompany) -> Iterable[RawPosting]:
        for job in payload["jobs"]:
            places = [
                ", ".join(p for p in (loc.get("city"), loc.get("country")) if p)
                for loc in job.get("locations") or []
                if not loc.get("hidden")
            ]
            location = "; ".join(p for p in places if p) or ", ".join(
                p for p in (job.get("city"), job.get("country")) if p
            ) or None
            yield RawPosting(
                external_id=f"{company.slug}:{job['shortcode']}",
                url=job.get("url") or job["shortlink"],
                title=clean(job["title"]) or "(untitled)",
                company_name=company.name,
                location_text=clean(location),
                work_mode="remote" if job.get("telecommuting") else guess_work_mode(location),
                description=html_to_text(job.get("description")),
                contract_type=job.get("employment_type") or None,
                posted_at=from_iso(job.get("published_on") or job.get("created_at")),
                raw=job,
                ats_kind=self.source_key,
                ats_slug=company.slug,
            )


def _decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


ATS_COLLECTORS: list[AtsCollector] = [
    GreenhouseCollector(),
    LeverCollector(),
    LeverEuCollector(),
    AshbyCollector(),
    WorkableCollector(),
]
