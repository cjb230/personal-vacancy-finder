"""Collector for companies that list jobs as links on a plain careers page (ats_kind 'page').

ats_slug is the careers page URL and link_pattern a regex that picks out the job links,
matched against each href as written in the page. Only the listing is read, not each advert,
so these postings have a title and maybe a location but no description.
"""

import re
from collections.abc import Iterable
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


def _card(link: Tag, pattern: re.Pattern) -> Tag:
    """The largest ancestor that still holds only this one job: the job's card on the page."""
    href = link["href"]
    card = link
    for parent in list(link.parents)[:_MAX_LEVELS]:
        others = {a["href"] for a in parent.find_all("a", href=pattern)} - {href}
        if others:
            break
        card = parent
        if parent.find(_is_title):
            break
    return card


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
        pattern = re.compile(company.link_pattern)
        soup = BeautifulSoup(payload, "html.parser")
        seen = set()
        for link in soup.find_all("a", href=pattern):
            url = urljoin(company.slug, link["href"]).split("#")[0]
            if url in seen:
                continue
            seen.add(url)
            card = _card(link, pattern)
            title = _find_text(link, _is_title) or _find_text(card, _is_title) or _text(link)
            location = _find_text(card, lambda t: _LOCATION_CLASS.search(" ".join(t.get("class") or [])))
            yield RawPosting(
                external_id=url,
                url=url,
                title=title or "(untitled)",
                company_name=company.name,
                location_text=location,
                work_mode=guess_work_mode(location, title),
                ats_kind=self.source_key,
                ats_slug=company.slug,
            )
