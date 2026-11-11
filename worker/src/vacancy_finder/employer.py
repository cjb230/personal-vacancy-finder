"""Following a company's own careers page, so its adverts override the boards' copies.

Used by the results page's Employer link button (web.py) and by find_employer_adverts.py.
"""

from collections.abc import Iterable

import httpx
import psycopg

from .collectors import COLLECTORS, Collector
from .collectors.ats import USER_AGENT, TrackedCompany
from .models import RawPosting
from .normalize import normalize_title
from .store import ensure_source, upsert_posting
from .matching import TWIN_SIMILARITY, title_similarity

READ_ERRORS = (httpx.HTTPError, ValueError, KeyError, TypeError)


def client() -> httpx.Client:
    return httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def collector_for(kind: str) -> Collector:
    return next(c for c in COLLECTORS if c.source_key == kind)


def read_careers_page(http: httpx.Client, kind: str, company: TrackedCompany) -> list[RawPosting]:
    """Every advert on the company's careers page. Raises one of READ_ERRORS if it can't be read."""
    collector = collector_for(kind)
    return list(collector.parse(collector.fetch_company(http, company), company))


def same_job(a: str, b: str) -> bool:
    """The titles name the same job, by the rule scoring uses for twins."""
    return normalize_title(a) == normalize_title(b) or title_similarity(a, b) >= TWIN_SIMILARITY


def lists_any(postings: Iterable[RawPosting], titles: Iterable[str]) -> list[str]:
    """The titles among `titles` that the careers page lists."""
    page = [p.title for p in postings]
    return [t for t in titles if any(same_job(t, p) for p in page)]


def follow_careers_page(conn: psycopg.Connection, company: TrackedCompany, kind: str,
                        postings: list[RawPosting], aggregator: bool = False) -> None:
    """Track the company on `kind` (ats_slug = company.slug) from now on and store the adverts
    already read from its page. With aggregator, the company is marked as one whose board lists
    other companies' jobs, so its adverts don't count as definitive. Commits; the caller
    rescores."""
    conn.execute("UPDATE companies SET ats_kind = %s, ats_slug = %s, tracked = true,"
                 " aggregator = aggregator OR %s WHERE id = %s",
                 (kind, company.slug, aggregator, company.id))
    collector = collector_for(kind)
    source_id = ensure_source(conn, collector.source_key, collector.source_name,
                              collector.source_kind)
    for posting in postings:
        upsert_posting(conn, source_id, posting)
    conn.commit()
