"""Collector for companies that list jobs as links on a plain careers page (ats_kind 'page').

ats_slug is the careers page URL and link_pattern a regex that picks out the job links,
matched against each href as written in the page. Only the listing is read, not each advert,
so these postings have a title and maybe a location but no description.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from ..models import RawPosting
from .ats import AtsCollector, TrackedCompany, clean, guess_work_mode

_HEADING = re.compile(r"^h[1-6]$")
_TITLE_CLASS = re.compile(r"heading|title", re.I)
_LOCATION_CLASS = re.compile(r"location", re.I)
# How far up from a job link to look for its title, e.g. a sibling heading in the same card.
_MAX_LEVELS = 4


def _text(tag: Tag) -> str | None:
    return clean(" ".join(tag.get_text(" ").split()))


def _is_title(tag: Tag) -> bool:
    return bool(_HEADING.match(tag.name) or _TITLE_CLASS.search(" ".join(tag.get("class") or [])))


def _find_text(scope: Tag, wanted) -> str | None:
    for tag in scope.find_all(wanted):
        if text := _text(tag):
            return text
    return None


def _card(link: Tag, pattern: re.Pattern, key) -> Tag:
    """The largest ancestor that still holds only this one job: the job's card on the page."""
    this = key(link["href"])
    card = link
    for parent in list(link.parents)[:_MAX_LEVELS]:
        others = {key(a["href"]) for a in parent.find_all("a", href=pattern)} - {this}
        if others:
            break
        card = parent
        if parent.find(_is_title):
            break
    return card


def _location(card: Tag) -> str | None:
    """The first location-classed element's text. When it holds several parts, e.g. the same
    city in English and Polish, the first part."""
    for tag in card.find_all(lambda t: _LOCATION_CLASS.search(" ".join(t.get("class") or []))):
        parts = [text for child in tag.find_all(True) if not child.find(True) and (text := _text(child))]
        if text := (parts[0] if parts else _text(tag)):
            return text
    return None


@dataclass
class JobLink:
    url: str
    title: str | None
    location: str | None
    card: Tag  # the element holding this job and no other


def find_job_links(html: str, base_url: str, pattern: re.Pattern | str,
                   key=lambda href: href.split("#")[0]) -> list[JobLink]:
    """Each distinct job link on a page or email, with the title and location around it.

    `key` says which hrefs are the same job, e.g. by job id when links carry tracking
    parameters. Of several links to one job, the first that yields a title wins.
    """
    pattern = re.compile(pattern)
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, JobLink] = {}
    for link in soup.find_all("a", href=pattern):
        job = key(link["href"])
        if job in found and found[job].title:
            continue
        card = _card(link, pattern, key)
        title = _find_text(link, _is_title) or _find_text(card, _is_title) or _text(link)
        url = urljoin(base_url, link["href"]).split("#")[0]
        if job not in found or title:
            found[job] = JobLink(url, title, _location(card), card)
    return list(found.values())


class PageCollector(AtsCollector):
    source_key = "page"
    source_name = "Company careers pages"

    def fetch(self, client: httpx.Client, slug: str) -> Any:
        response = client.get(slug)
        response.raise_for_status()
        return response.text

    def parse(self, payload: str, company: TrackedCompany) -> Iterable[RawPosting]:
        if not company.link_pattern:
            raise ValueError(f"{company.name} has no link_pattern")
        for job in find_job_links(payload, company.slug, company.link_pattern):
            yield RawPosting(
                external_id=job.url,
                url=job.url,
                title=job.title or "(untitled)",
                company_name=company.name,
                location_text=job.location,
                work_mode=guess_work_mode(job.location, job.title),
                ats_kind=self.source_key,
                ats_slug=company.slug,
            )
