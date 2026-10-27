"""Just Join IT (justjoin.it), the largest Polish IT job board.

Its robots.txt disallows /api/, so this collector uses only what the site
publishes for crawlers: the sitemap of active adverts, and each advert's page.
Each page carries a schema.org JobPosting block plus the app's own offer data,
which has the fields we care about (B2B vs permanent pay, workplace type,
seniority, required language).

There are ~11,000 live adverts, so the collector:
- only opens pages whose URL slug matches one of KEYWORDS (role words), and
- only re-opens a page when its sitemap lastmod changes. Unchanged adverts are
  marked as still seen without a request.
Pages are fetched one at a time with a pause between them.
"""

import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
import psycopg

from ..models import RawPosting
from ..normalize import looks_polish
from .ats import USER_AGENT, clean, from_iso, html_to_text

log = logging.getLogger(__name__)

SITEMAP_URL = "https://justjoin.it/sitemaps/active-jobs.xml"
OFFER_PREFIX = "https://justjoin.it/job-offer/"
_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# Matched as whole hyphen-separated words against the URL slug, which holds company,
# title, city and category. So "architect" does not match the "architecture" category,
# and plain "manager" is left out because it mostly brings in sales and project roles.
DEFAULT_KEYWORDS = (
    "python", "engineering-manager", "engineering-lead", "head-of-engineering",
    "head-of-software", "head-of-technology", "director", "staff", "principal",
    "tech-lead", "team-lead", "technical-lead", "architect", "cto", "vp",
)

_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "office": "onsite"}

_LD_JSON = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_NEXT_F = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', re.S)


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    lastmod: str | None

    @property
    def slug(self) -> str:
        return self.url.removeprefix(OFFER_PREFIX).strip("/")


def parse_sitemap(xml: bytes) -> tuple[list[str], list[SitemapEntry]]:
    """Returns (child sitemap URLs, advert entries) from a sitemap index or url set."""
    root = ET.fromstring(xml)
    children = [loc.text.strip() for loc in root.findall("sm:sitemap/sm:loc", _NS)]
    entries = []
    for url in root.findall("sm:url", _NS):
        loc = url.findtext("sm:loc", namespaces=_NS)
        if loc and loc.strip().startswith(OFFER_PREFIX):
            entries.append(SitemapEntry(loc.strip(), url.findtext("sm:lastmod", namespaces=_NS)))
    return children, entries


def find_job_posting(page: str) -> dict[str, Any] | None:
    for block in _LD_JSON.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def find_offer(page: str, slug: str) -> dict[str, Any] | None:
    """The app's offer object for this slug, from the self.__next_f payloads.

    The page also embeds similar offers, so match on slug and require employmentTypes.
    """
    needle = re.compile(r'"slug"\s*:\s*' + re.escape(json.dumps(slug)))
    decoder = json.JSONDecoder()
    for chunk in _NEXT_F.findall(page):
        try:
            text = json.loads(chunk)
        except ValueError:
            continue
        for match in needle.finditer(text):
            at = match.start()
            brace = text.rfind("{", 0, at)
            if brace == -1:
                continue
            try:
                obj, _ = decoder.raw_decode(text, brace)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("slug") == slug and "employmentTypes" in obj:
                return obj
    return None


def pick_salary(employment_types: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The employer's own figures (not conversions), preferring B2B."""
    originals = [e for e in employment_types if e.get("currencySource") == "original"]
    candidates = originals or employment_types
    for e in candidates:
        if e.get("type") == "b2b" and e.get("from") is not None:
            return e
    return next((e for e in candidates if e.get("from") is not None), None)


def salary_from_job_posting(posting: dict[str, Any]) -> dict[str, Any] | None:
    """Fallback when the app data has no figures: schema.org baseSalary, in the same shape."""
    base = posting.get("baseSalary") or {}
    value = base.get("value") or {}
    if value.get("minValue") is None:
        return None
    return {
        "from": value.get("minValue"),
        "to": value.get("maxValue"),
        "currency": base.get("currency"),
        "unit": (value.get("unitText") or "").lower(),
    }


def _decimal(value: Any) -> Decimal | None:
    return Decimal(str(round(value, 2))) if value is not None else None


def parse_offer_page(page: str, entry: SitemapEntry) -> RawPosting | None:
    posting = find_job_posting(page)
    offer = find_offer(page, entry.slug) or {}
    if not posting and not offer:
        return None
    posting = posting or {}

    employment = offer.get("employmentTypes") or []
    salary = pick_salary(employment) or salary_from_job_posting(posting) or {}
    contract_types = sorted({e["type"] for e in employment if e.get("type")})
    workplace = (offer.get("workplaceType") or {}).get("value")
    location = offer.get("city") or (
        (posting.get("jobLocation") or {}).get("address") or {}
    ).get("addressLocality")
    cities = [m.get("city") for m in offer.get("multilocation") or [] if m.get("city")]
    if len(cities) > 1:
        location = "; ".join(dict.fromkeys(cities))

    description = html_to_text(posting.get("description"))
    # The language the advert is written in. (offer["languages"] is what the job requires.)
    language = "pl" if looks_polish(description) else "en"

    return RawPosting(
        external_id=entry.slug,
        url=entry.url,
        title=clean(offer.get("title") or posting.get("title")) or "(untitled)",
        company_name=clean(offer.get("companyName"))
        or clean((posting.get("hiringOrganization") or {}).get("name"))
        or "(unknown)",
        location_text=location,
        work_mode=_WORKPLACE.get(workplace or "", "unknown"),
        description=description,
        language=language,
        contract_type=";".join(contract_types) or None,
        salary_min=_decimal(salary.get("from")),
        salary_max=_decimal(salary.get("to")),
        salary_currency=salary.get("currency"),
        salary_period=salary.get("unit") if salary.get("unit") in ("hour", "day", "month", "year") else None,
        posted_at=from_iso(offer.get("publishedAt") or posting.get("datePosted")),
        raw={
            "sitemap_lastmod": entry.lastmod,
            "offer": offer,
            "job_posting": {k: v for k, v in posting.items() if k != "description"},
        },
    )


class JustJoinItCollector:
    source_key = "justjoinit"
    source_name = "Just Join IT"
    source_kind = "board"

    def __init__(
        self,
        client: httpx.Client | None = None,
        keywords: tuple[str, ...] | None = None,
        delay_seconds: float | None = None,
        max_pages: int | None = None,
    ) -> None:
        self._client = client
        env_keywords = os.environ.get("JUSTJOINIT_KEYWORDS")
        self.keywords = keywords or (
            tuple(k.strip() for k in env_keywords.split(",") if k.strip())
            if env_keywords else DEFAULT_KEYWORDS
        )
        self.delay = delay_seconds if delay_seconds is not None else float(
            os.environ.get("JUSTJOINIT_DELAY_SECONDS", "2")
        )
        self.max_pages = max_pages or int(os.environ.get("JUSTJOINIT_MAX_PAGES", "500"))
        self._incomplete: set[int] = set()

    def incomplete_company_ids(self) -> set[int]:
        return self._incomplete

    def wanted(self, entry: SitemapEntry) -> bool:
        slug = f"-{entry.slug.lower()}-"
        return any(f"-{k.strip('-')}-" in slug for k in self.keywords)

    def sitemap_entries(self, client: httpx.Client) -> list[SitemapEntry]:
        pending, entries = [SITEMAP_URL], []
        while pending:
            response = client.get(pending.pop(0))
            response.raise_for_status()
            children, found = parse_sitemap(response.content)
            pending += children
            entries += found
        return entries

    def collect(self, conn: psycopg.Connection) -> Iterator[RawPosting]:
        client = self._client or httpx.Client(
            timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        try:
            entries = [e for e in self.sitemap_entries(client) if self.wanted(e)]
            known = known_lastmods(conn, self.source_key)
            unchanged = [e.slug for e in entries if e.slug in known and known[e.slug] == e.lastmod]
            touch_postings(conn, self.source_key, unchanged)
            to_fetch = [e for e in entries if e.slug not in unchanged]
            log.info(
                "justjoinit: %d matching adverts, %d unchanged, %d to fetch (cap %d)",
                len(entries), len(unchanged), len(to_fetch), self.max_pages,
            )
            if len(to_fetch) > self.max_pages:
                # The rest are fetched on later runs. Keep their existing rows open meanwhile.
                touch_postings(conn, self.source_key, [e.slug for e in to_fetch[self.max_pages:]])
                to_fetch = to_fetch[: self.max_pages]

            for i, entry in enumerate(to_fetch):
                if i:
                    time.sleep(self.delay)
                try:
                    response = client.get(entry.url)
                    if response.status_code == 404:
                        continue  # taken down since the sitemap was built
                    response.raise_for_status()
                    posting = parse_offer_page(response.text, entry)
                except (httpx.HTTPError, ValueError) as exc:
                    log.warning("justjoinit: %s failed: %r", entry.url, exc)
                    touch_postings(conn, self.source_key, [entry.slug])
                    continue
                if posting is None:
                    log.warning("justjoinit: no offer data found in %s", entry.url)
                    touch_postings(conn, self.source_key, [entry.slug])
                    continue
                # Polish-language adverts are kept, with language 'pl', so they are not
                # re-fetched every run; matching (step 4) leaves them out.
                yield posting
        finally:
            if self._client is None:
                client.close()


def known_lastmods(conn: psycopg.Connection, source_key: str) -> dict[str, str | None]:
    rows = conn.execute(
        """
        SELECT p.external_id, p.raw ->> 'sitemap_lastmod'
        FROM postings p JOIN sources s ON s.id = p.source_id
        WHERE s.key = %s AND p.closed_at IS NULL
        """,
        (source_key,),
    ).fetchall()
    return dict(rows)


def touch_postings(conn: psycopg.Connection, source_key: str, external_ids: list[str]) -> None:
    """Mark existing postings as seen in this run without re-fetching them."""
    if not external_ids:
        return
    conn.execute(
        """
        UPDATE postings p SET last_seen_at = now()
        FROM sources s
        WHERE s.id = p.source_id AND s.key = %s AND p.external_id = ANY(%s)
        """,
        (source_key, external_ids),
    )
    conn.execute(
        """
        UPDATE jobs j SET last_seen_at = now()
        FROM postings p JOIN sources s ON s.id = p.source_id
        WHERE p.job_id = j.id AND s.key = %s AND p.external_id = ANY(%s)
        """,
        (source_key, external_ids),
    )
