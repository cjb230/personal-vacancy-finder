"""Space-Careers (space-careers.com), a European space-industry job board.

The site has no feed or API, but its sitemap lists every live advert (about 200) with a
lastmod date, so the collector reads the sitemap and opens only adverts that are new or
changed since the last run. Unchanged adverts are marked as still seen without a request.

Each advert page has the title as its <h1>, the company as its "jobs at" link, the
workplace (on-site / hybrid / remote) as one of its "More Job Searches" links, and the
location in its meta description: "Apply for the {title} Job, {company}, {location}.
Space Jobs from space-careers.com". Pages carry no pay or closing date.
"""

import html
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import psycopg

from ..models import RawPosting
from ..normalize import detect_language
from .ats import USER_AGENT, clean, html_to_text
from .justjoinit import SitemapEntry, known_lastmods, touch_postings

log = logging.getLogger(__name__)

SITEMAP_URL = "https://www.space-careers.com/sitemap.xml"
_JOB_URL = re.compile(r"^https://www\.space-careers\.com/job_display/(\d+)/")

_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_COMPANY = re.compile(r'<a href="/jobs_at/[^"]+">([^<]+)</a>')
_WORKPLACE = re.compile(r'<a href="/jobs/(on-site|hybrid|remote)">')
_META_DESCRIPTION = re.compile(r'<meta name="description" content="([^"]*)"')
_DESCRIPTION = re.compile(r"<!-- Job Description start -->(.*?)<!-- Job Description end -->", re.S)
_MODES = {"on-site": "onsite", "hybrid": "hybrid", "remote": "remote"}


def parse_sitemap(xml: bytes) -> list[SitemapEntry]:
    # The site declares the namespace as https://www.sitemaps.org/..., not the standard
    # http://, so match on local names whatever the namespace.
    entries = []
    for url in ET.fromstring(xml).findall("{*}url"):
        loc = (url.findtext("{*}loc") or "").strip()
        if _JOB_URL.match(loc):
            entries.append(SitemapEntry(loc, url.findtext("{*}lastmod")))
    return entries


def job_id(entry: SitemapEntry) -> str:
    return _JOB_URL.match(entry.url).group(1)


def location_from_meta(meta: str, title: str, company: str) -> str | None:
    text = html.unescape(meta)
    text = re.sub(r"\.?\s*Space Jobs from space-careers\.com\s*$", "", text)
    prefix = f"Apply for the {title} Job, {company}, "
    if not text.startswith(prefix):
        return None
    return text.removeprefix(prefix).strip() or None


def parse_job_page(page: str, entry: SitemapEntry) -> RawPosting | None:
    if not (h1 := _H1.search(page)):
        return None
    title = clean(re.sub(r"<[^>]+>", "", h1.group(1)).strip())
    company_match = _COMPANY.search(page)
    company = clean(company_match.group(1).strip()) if company_match else None
    meta = _META_DESCRIPTION.search(page)
    location = (
        location_from_meta(meta.group(1), title, company) if meta and title and company else None
    )
    workplace = _WORKPLACE.search(page)
    body = _DESCRIPTION.search(page)
    description = html_to_text(body.group(1)) if body else None
    lastmod = None
    if entry.lastmod:
        lastmod = datetime.fromisoformat(entry.lastmod)
        lastmod = lastmod if lastmod.tzinfo else lastmod.replace(tzinfo=UTC)
    return RawPosting(
        external_id=job_id(entry),
        url=entry.url,
        title=title or "(untitled)",
        company_name=company or "(unknown)",
        location_text=location,
        work_mode=_MODES[workplace.group(1)] if workplace else "unknown",
        description=description,
        language=detect_language(description),
        # The sitemap's lastmod is the only date the site gives; for a new advert it is
        # the day it went up.
        posted_at=lastmod,
        raw={"sitemap_lastmod": entry.lastmod},
    )


class SpaceCareersCollector:
    source_key = "space-careers"
    source_name = "Space-Careers"
    source_kind = "board"

    def __init__(
        self,
        client: httpx.Client | None = None,
        delay_seconds: float | None = None,
        max_pages: int | None = None,
    ) -> None:
        self._client = client
        self.delay = delay_seconds if delay_seconds is not None else float(
            os.environ.get("SPACECAREERS_DELAY_SECONDS", "2")
        )
        self.max_pages = max_pages or int(os.environ.get("SPACECAREERS_MAX_PAGES", "300"))

    def incomplete_company_ids(self) -> set[int]:
        return set()

    def sitemap_entries(self, client: httpx.Client) -> list[SitemapEntry]:
        response = client.get(SITEMAP_URL)
        response.raise_for_status()
        return parse_sitemap(response.content)

    def collect(self, conn: psycopg.Connection) -> Iterator[RawPosting]:
        client = self._client or httpx.Client(
            timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        try:
            entries = self.sitemap_entries(client)
            known = known_lastmods(conn, self.source_key)
            unchanged = [job_id(e) for e in entries if known.get(job_id(e), "") == e.lastmod]
            touch_postings(conn, self.source_key, unchanged)
            to_fetch = [e for e in entries if job_id(e) not in set(unchanged)]
            log.info("space-careers: %d adverts, %d unchanged, %d to fetch (cap %d)",
                     len(entries), len(unchanged), len(to_fetch), self.max_pages)
            if len(to_fetch) > self.max_pages:
                touch_postings(conn, self.source_key, [job_id(e) for e in to_fetch[self.max_pages:]])
                to_fetch = to_fetch[: self.max_pages]

            for i, entry in enumerate(to_fetch):
                if i:
                    time.sleep(self.delay)
                try:
                    response = client.get(entry.url)
                    if response.status_code == 404:
                        continue  # taken down since the sitemap was built
                    response.raise_for_status()
                    posting = parse_job_page(response.text, entry)
                except (httpx.HTTPError, ValueError) as exc:
                    log.warning("space-careers: %s failed: %r", entry.url, exc)
                    touch_postings(conn, self.source_key, [job_id(entry)])
                    continue
                if posting is None:
                    log.warning("space-careers: no job found in %s", entry.url)
                    touch_postings(conn, self.source_key, [job_id(entry)])
                    continue
                yield posting
        finally:
            if self._client is None:
                client.close()
