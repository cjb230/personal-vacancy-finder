"""Remote-job boards with public feeds: Himalayas, RemoteOK, We Work Remotely,
Arbeitnow, and the monthly Hacker News "Who is hiring?" thread.

All were checked on 2026-10-27: robots.txt allows these paths. RemoteOK and
Arbeitnow ask for a link back; each posting's url points at their page.
(Remotive was left out: its robots.txt disallows /api/ and the free API
returns only 10 day-old jobs.)

These feeds list only recent jobs, so a posting is closed after it has been
missing for `stale_after_days`, not on its first absence.
"""

import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import psycopg

from ..models import RawPosting
from .ats import USER_AGENT, clean, from_iso, guess_work_mode, html_to_text

log = logging.getLogger(__name__)


def from_epoch(value: int | float | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value else None


def fix_mojibake(text: str | None) -> str | None:
    """Repair UTF-8 that was decoded as Latin-1 (e.g. 'Â\xa0', 'â€™'), seen on RemoteOK."""
    if not text or not re.search("[ÂÃâ][\x80-\xbf€™œ\x9d]", text):
        return text
    try:
        return text.encode("cp1252", errors="strict").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        try:
            return text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text


def _money(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value not in (None, 0, "") else None


class FeedCollector:
    """Base for feed collectors: shared client, pacing, and no per-company tracking."""

    source_kind = "board"
    stale_after_days = 14
    source_key: str
    source_name: str

    def __init__(self, client: httpx.Client | None = None, delay_seconds: float = 2) -> None:
        self._client = client
        self.delay = delay_seconds
        self._requests = 0

    def incomplete_company_ids(self) -> set[int]:
        return set()

    def get(self, client: httpx.Client, url: str, **params: Any) -> httpx.Response:
        if self._requests:
            time.sleep(self.delay)
        self._requests += 1
        response = client.get(url, params=params or None)
        response.raise_for_status()
        return response

    def collect(self, conn: psycopg.Connection) -> Iterator[RawPosting]:
        client = self._client or httpx.Client(
            timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        self._requests = 0
        try:
            yield from self.postings(client)
        finally:
            if self._client is None:
                client.close()

    def postings(self, client: httpx.Client) -> Iterator[RawPosting]:
        raise NotImplementedError


class HimalayasCollector(FeedCollector):
    """Searches Himalayas for each role query, limited to jobs open to people in Poland."""

    source_key = "himalayas"
    source_name = "Himalayas"
    stale_after_days = 21
    url = "https://himalayas.app/jobs/api/search"
    default_queries = (
        "python", "engineering manager", "staff engineer", "head of engineering",
        "director of engineering", "tech lead", "principal engineer",
    )
    _PERIODS = {"annual": "year", "yearly": "year", "monthly": "month", "hourly": "hour",
                "daily": "day"}

    def __init__(self, *args, queries=None, max_pages=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        env = os.environ.get("HIMALAYAS_QUERIES")
        self.queries = queries or (
            tuple(q.strip() for q in env.split(",") if q.strip()) if env else self.default_queries
        )
        self.max_pages = max_pages or int(os.environ.get("HIMALAYAS_MAX_PAGES", "5"))

    def postings(self, client: httpx.Client) -> Iterator[RawPosting]:
        seen: set[str] = set()
        for query in self.queries:
            for page in range(1, self.max_pages + 1):
                jobs = self.get(client, self.url, q=query, country="Poland", page=page).json()
                jobs = jobs.get("jobs") or []
                for job in jobs:
                    if job["guid"] not in seen:
                        seen.add(job["guid"])
                        yield self.parse(job)
                if len(jobs) < 20:
                    break

    def parse(self, job: dict[str, Any]) -> RawPosting:
        restrictions = job.get("locationRestrictions") or []
        return RawPosting(
            external_id=job["guid"],
            url=job["guid"],
            title=clean(job["title"]) or "(untitled)",
            company_name=clean(job.get("companyName")) or "(unknown)",
            location_text="; ".join(restrictions) or "Anywhere",
            work_mode="remote",
            description=html_to_text(job.get("description")),
            contract_type=job.get("employmentType"),
            salary_min=_money(job.get("minSalary")),
            salary_max=_money(job.get("maxSalary")),
            salary_currency=job.get("currency"),
            salary_period=self._PERIODS.get((job.get("salaryPeriod") or "").lower()),
            posted_at=from_epoch(job.get("pubDate")),
            raw={k: v for k, v in job.items() if k != "description"},
        )


class RemoteOkCollector(FeedCollector):
    """RemoteOK's feed of its latest ~100 remote jobs."""

    source_key = "remoteok"
    source_name = "Remote OK"
    url = "https://remoteok.com/api"

    def postings(self, client: httpx.Client) -> Iterator[RawPosting]:
        data = self.get(client, self.url).json()
        for job in data:
            if "id" not in job or "position" not in job:
                continue  # the first element is the legal notice
            yield self.parse(job)

    def parse(self, job: dict[str, Any]) -> RawPosting:
        return RawPosting(
            external_id=str(job["id"]),
            url=job.get("url") or f"https://remoteok.com/remote-jobs/{job['slug']}",
            title=clean(fix_mojibake(job["position"])) or "(untitled)",
            company_name=clean(fix_mojibake(job.get("company"))) or "(unknown)",
            location_text=clean(fix_mojibake(job.get("location"))) or "Anywhere",
            work_mode="remote",
            description=html_to_text(fix_mojibake(job.get("description"))),
            salary_min=_money(job.get("salary_min")),
            salary_max=_money(job.get("salary_max")),
            salary_currency="USD" if job.get("salary_min") else None,
            salary_period="year" if job.get("salary_min") else None,
            posted_at=from_iso(job.get("date")),
            raw={k: v for k, v in job.items() if k != "description"},
        )


class WeWorkRemotelyCollector(FeedCollector):
    """We Work Remotely's per-category RSS feeds."""

    source_key = "weworkremotely"
    source_name = "We Work Remotely"
    feeds = (
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    )

    def postings(self, client: httpx.Client) -> Iterator[RawPosting]:
        seen: set[str] = set()
        for feed in self.feeds:
            root = ET.fromstring(self.get(client, feed).content)
            for item in root.iter("item"):
                posting = self.parse(item)
                if posting.external_id not in seen:
                    seen.add(posting.external_id)
                    yield posting

    def parse(self, item: ET.Element) -> RawPosting:
        def text(tag: str) -> str | None:
            value = item.findtext(tag)
            return value.strip() if value else None

        heading = clean(text("title")) or ""
        company, _, title = heading.partition(": ")
        if not title:
            company, title = "(unknown)", heading
        region = clean(text("region"))
        published = text("pubDate")
        link = text("link") or text("guid")
        return RawPosting(
            external_id=text("guid") or link,
            url=link,
            title=title.strip() or "(untitled)",
            company_name=company.strip() or "(unknown)",
            location_text=region,
            work_mode="remote",
            description=html_to_text(text("description")),
            posted_at=parsedate_to_datetime(published) if published else None,
            raw={"region": region, "category": text("category"), "type": text("type")},
        )


class ArbeitnowCollector(FeedCollector):
    """Arbeitnow's job board API. Mostly on-site jobs in Germany, so only remote
    jobs and jobs in Poland are kept."""

    source_key = "arbeitnow"
    source_name = "Arbeitnow"
    url = "https://www.arbeitnow.com/api/job-board-api"

    def __init__(self, *args, max_pages=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.max_pages = max_pages or int(os.environ.get("ARBEITNOW_MAX_PAGES", "10"))

    def postings(self, client: httpx.Client) -> Iterator[RawPosting]:
        for page in range(1, self.max_pages + 1):
            body = self.get(client, self.url, page=page).json()
            for job in body.get("data") or []:
                location = job.get("location") or ""
                if job.get("remote") or "poland" in location.lower():
                    yield self.parse(job)
            if not (body.get("links") or {}).get("next"):
                break

    def parse(self, job: dict[str, Any]) -> RawPosting:
        location = clean(job.get("location"))
        return RawPosting(
            external_id=job["slug"],
            url=job["url"],
            title=clean(job["title"]) or "(untitled)",
            company_name=clean(job.get("company_name")) or "(unknown)",
            location_text=location,
            work_mode="remote" if job.get("remote") else guess_work_mode(location),
            description=html_to_text(job.get("description")),
            contract_type=", ".join(job.get("job_types") or []) or None,
            posted_at=from_epoch(job.get("created_at")),
            raw={k: v for k, v in job.items() if k != "description"},
        )


class HackerNewsHiringCollector(FeedCollector):
    """Top-level posts in the latest monthly "Ask HN: Who is hiring?" thread.

    Posts are free text; the first line is usually "Company | Role | Location | ...".
    """

    source_key = "hn-whoishiring"
    source_name = "Hacker News: Who is hiring?"
    stale_after_days = 45  # a new thread starts each month
    search_url = "https://hn.algolia.com/api/v1/search_by_date"
    item_url = "https://hn.algolia.com/api/v1/items/{id}"

    def postings(self, client: httpx.Client) -> Iterator[RawPosting]:
        hits = self.get(
            client, self.search_url, tags="story,author_whoishiring", hitsPerPage=6
        ).json()["hits"]
        thread = next(
            (h for h in hits if (h.get("title") or "").startswith("Ask HN: Who is hiring?")), None
        )
        if thread is None:
            raise ValueError("no 'Who is hiring?' thread found")
        story = self.get(client, self.item_url.format(id=thread["objectID"])).json()
        for comment in story.get("children") or []:
            if comment.get("text"):
                yield self.parse(comment, story.get("title"))

    def parse(self, comment: dict[str, Any], thread_title: str | None = None) -> RawPosting:
        text = html_to_text(comment["text"]) or ""
        first_line = text.split("\n", 1)[0]
        parts = [p.strip() for p in first_line.split("|") if p.strip()]
        company = parts[0] if parts else "(unknown)"
        role = parts[1] if len(parts) > 1 else first_line
        return RawPosting(
            external_id=str(comment["id"]),
            url=f"https://news.ycombinator.com/item?id={comment['id']}",
            title=role[:200] or "(untitled)",
            company_name=company[:200],
            location_text=" | ".join(parts[2:])[:300] or None,
            work_mode=guess_work_mode(first_line),
            description=text,
            posted_at=from_iso(comment.get("created_at")),
            raw={"author": comment.get("author"), "thread": thread_title, "first_line": first_line},
        )


REMOTE_COLLECTORS = [
    HimalayasCollector(),
    RemoteOkCollector(),
    WeWorkRemotelyCollector(),
    ArbeitnowCollector(),
    HackerNewsHiringCollector(),
]
