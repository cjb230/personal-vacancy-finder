"""Finds the employer's own advert for each Himalayas job that is Interested, or an unreviewed
Maybe, and rescores it: the employer's careers page then counts over Himalayas' copy.

Himalayas' advert pages (and the Apply link on them) sit behind a Cloudflare challenge, so the
careers page is looked for another way, company by company:

1. links to a careers system in any advert already collected for the company;
2. the company's name tried as a slug on each careers system the probe knows (probe.py).

A careers page only counts if it lists one of the company's jobs by the same title (the twin
rule scoring uses), so a common name matching some other company's board is not taken. A page
found is followed from then on, exactly as the Employer link button does, and the company's
jobs are rescored. Jobs it can't place are listed with their Himalayas link, to use the
Employer link button on by hand. Comeet pages can only be found from a link.

Usage, on the machine running the worker:
    docker compose exec worker python -m vacancy_finder.find_employer_adverts --dry-run
    docker compose exec worker python -m vacancy_finder.find_employer_adverts
"""

import argparse
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import psycopg

from . import employer
from .collectors.ats import TrackedCompany
from .discover import find_boards
from .matching import score_jobs
from .models import RawPosting
from .probe import PROBES, candidate_slugs

log = logging.getLogger(__name__)

# The jobs the Employer link button is offered on: an open Himalayas posting, no posting from
# an employer careers page, at a company whose careers page isn't followed, and Interested or
# an unreviewed Maybe.
TARGETS = """
SELECT j.id, j.title, c.id, c.name, s.verdict, s.score,
       (SELECT p.url FROM postings p JOIN sources so ON so.id = p.source_id
        WHERE p.job_id = j.id AND p.closed_at IS NULL AND so.key = 'himalayas'
        ORDER BY p.last_seen_at DESC LIMIT 1)
FROM jobs j
JOIN companies c ON c.id = j.company_id
JOIN job_scores s ON s.job_id = j.id
LEFT JOIN job_reviews r ON r.job_id = j.id
WHERE j.status = 'open'
  AND (r.decision = 'interested' OR (r.decision IS NULL AND s.verdict = 'maybe'))
  AND EXISTS (SELECT 1 FROM postings p JOIN sources so ON so.id = p.source_id
              WHERE p.job_id = j.id AND p.closed_at IS NULL AND so.key = 'himalayas')
  AND NOT EXISTS (SELECT 1 FROM postings p JOIN sources so ON so.id = p.source_id
                  WHERE p.job_id = j.id AND p.closed_at IS NULL AND so.kind = 'ats')
  AND NOT COALESCE(c.tracked AND c.ats_kind IS NOT NULL, false)
ORDER BY c.name, j.id
"""

# Every advert text, URL and raw record collected for a company, to look for careers links in.
COMPANY_TEXT = """
SELECT string_agg(concat_ws(' ', p.url, p.description, p.raw::text), ' ')
FROM postings p JOIN jobs j ON j.id = p.job_id WHERE j.company_id = %s
"""


@dataclass
class Job:
    id: int
    title: str
    verdict: str
    score: int
    himalayas_url: str | None
    after: tuple[str, int, list[str]] | None = None  # verdict, score, reasons once rescored


@dataclass
class Company:
    id: int
    name: str
    jobs: list[Job] = field(default_factory=list)
    found: tuple[str, str, str] | None = None  # (kind, slug, how it was found)
    adverts: int = 0
    listed: list[str] = field(default_factory=list)  # titles of our jobs the page lists
    rejected: list[str] = field(default_factory=list)  # boards found that list none of them


def targets(conn: psycopg.Connection) -> list[Company]:
    companies: dict[int, Company] = {}
    for job_id, title, company_id, name, verdict, score, url in conn.execute(TARGETS):
        company = companies.setdefault(company_id, Company(company_id, name))
        company.jobs.append(Job(job_id, title, verdict, score, url))
    return list(companies.values())


def candidates(conn: psycopg.Connection, company: Company):
    """(kind, slug, how) to try, links in adverts first, then guesses from the name."""
    text = conn.execute(COMPANY_TEXT, (company.id,)).fetchone()[0]
    for kind, slug in sorted(find_boards(text)):
        yield kind, slug, "a link in an advert"
    for collector, to_slug in PROBES:
        for guess in candidate_slugs(company.name):
            yield collector.source_key, to_slug(guess), "its name"


def find_page(conn: psycopg.Connection, http: httpx.Client,
              company: Company) -> list[RawPosting] | None:
    """The adverts of the first careers page listing one of the company's jobs, noting which
    page it was in company.found; None if there is none."""
    tried: set[tuple[str, str]] = set()
    done_kinds: set[str] = set()  # by name, one board per system, as the probe does
    for kind, slug, how in candidates(conn, company):
        if (kind, slug) in tried or (how == "its name" and kind in done_kinds):
            continue
        tried.add((kind, slug))
        tracked = TrackedCompany(company.id, company.name, slug)
        try:
            postings = employer.read_careers_page(http, kind, tracked)
        except employer.READ_ERRORS:
            continue
        if not postings:
            continue  # Workable and Ashby answer any made-up slug with an empty board
        if how == "its name":
            done_kinds.add(kind)
        listed = employer.lists_any(postings, [j.title for j in company.jobs])
        if not listed:
            company.rejected.append(f"{kind} {slug} ({len(postings)} adverts, none of these jobs)")
            continue
        company.found, company.adverts, company.listed = (kind, slug, how), len(postings), listed
        return postings
    return None


def run(conn: psycopg.Connection, http: httpx.Client, dry_run: bool = False,
        delay: float = 1.0, say: Callable[[str], None] = print) -> list[Company]:
    companies = targets(conn)
    say(f"{sum(len(c.jobs) for c in companies)} Himalayas jobs at {len(companies)} companies "
        "to check against the employer's own careers page.")
    for i, company in enumerate(companies):
        if i and delay:
            time.sleep(delay)
        postings = find_page(conn, http, company)
        if postings is not None and not dry_run:
            kind, slug, _ = company.found
            employer.follow_careers_page(conn, TrackedCompany(company.id, company.name, slug),
                                         kind, postings)
            score_jobs(conn, [j.id for j in company.jobs])
            for job in company.jobs:
                job.after = conn.execute(
                    "SELECT verdict, score, reasons FROM job_scores WHERE job_id = %s",
                    (job.id,)).fetchone()
        report(company, dry_run, say)
    resolved = sum(1 for c in companies if c.found)
    say(f"\nDone: careers page {'found' if dry_run else 'followed'} for {resolved} of "
        f"{len(companies)} companies.{' Nothing was changed (dry run).' if dry_run else ''}")
    return companies


def report(company: Company, dry_run: bool, say: Callable[[str], None]) -> None:
    if company.found is None:
        tried = f" Rejected: {'; '.join(company.rejected)}." if company.rejected else ""
        say(f"\n{company.name}: no careers page found.{tried}")
        for job in company.jobs:
            say(f"  {job.title} ({job.verdict} {job.score}): use Employer link on {job.himalayas_url}")
        return
    kind, slug, how = company.found
    say(f"\n{company.name}: {kind} {slug}, found from {how}, {company.adverts} adverts"
        f"{' (would follow it)' if dry_run else ', now followed'}.")
    for job in company.jobs:
        listed = job.title in company.listed
        if job.after is None:
            note = "listed there" if listed else "not listed there: would keep its score, flagged"
            say(f"  {job.title} ({job.verdict} {job.score}): {note}")
            continue
        verdict, score, reasons = job.after
        why = next((r for r in reasons if "employer" in r), "")
        if verdict == "no" and reasons:
            why = f"{reasons[0]}; {why}" if why else reasons[0]
        say(f"  {job.title}: {job.verdict} {job.score} -> {verdict} {score} ({why})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="look for careers pages and report, without changing anything")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between companies")
    args = parser.parse_args()
    logging.basicConfig(level="WARNING")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, employer.client() as http:
        run(conn, http, dry_run=args.dry_run, delay=args.delay)


if __name__ == "__main__":
    main()
