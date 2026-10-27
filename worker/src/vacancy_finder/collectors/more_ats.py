"""Collectors for more hosted careers systems: Personio, Teamtailor, Recruitee and Traffit.

Like the collectors in ats.py, each polls the tracked companies whose ats_kind matches.
What goes in ats_slug differs by system, because their feed addresses do:

- personio:   the board's host, e.g. "liveeo-gmbh.jobs.personio.de" (.de or .com)
- teamtailor: the careers site, e.g. "jobs.tibber.com" or "www.engineering-jobs.sener/en"
- recruitee:  the company slug, e.g. "up42gmbh"
- traffit:    the company slug, e.g. "cloudferro"
"""

import xml.etree.ElementTree as ET
from collections.abc import Iterable
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from ..models import RawPosting
from .ats import AtsCollector, TrackedCompany, _decimal, clean, from_iso, guess_work_mode, html_to_text


def fetch_xml(client: httpx.Client, url: str) -> ET.Element:
    response = client.get(url)
    response.raise_for_status()
    try:
        return ET.fromstring(response.content)
    except ET.ParseError as exc:  # a SyntaxError, which the shared loop doesn't catch
        raise ValueError(f"bad XML from {url}: {exc}") from exc


class PersonioCollector(AtsCollector):
    source_key = "personio"
    source_name = "Personio job boards"

    def feed_url(self, slug: str) -> str:
        return f"https://{slug}/xml"

    def fetch(self, client: httpx.Client, slug: str) -> Any:
        return fetch_xml(client, self.feed_url(slug))

    def parse(self, payload: ET.Element, company: TrackedCompany) -> Iterable[RawPosting]:
        for position in payload.iter("position"):
            def text(tag: str) -> str | None:
                return clean(position.findtext(tag))

            job_id = text("id")
            office = text("office")
            sections = [
                f"{clean(d.findtext('name')) or ''}\n{html_to_text(d.findtext('value')) or ''}".strip()
                for d in position.iter("jobDescription")
            ]
            yield RawPosting(
                external_id=f"{company.slug}:{job_id}",
                url=f"https://{company.slug}/job/{job_id}",
                title=text("name") or "(untitled)",
                company_name=company.name,
                location_text=office,
                work_mode=guess_work_mode(office),
                description="\n\n".join(s for s in sections if s) or None,
                contract_type=text("employmentType"),
                posted_at=from_iso(text("createdAt")),
                raw={tag: text(tag) for tag in (
                    "department", "seniority", "schedule", "yearsOfExperience", "keywords",
                    "occupation", "occupationCategory", "recruitingCategory", "subcompany",
                )},
                ats_kind=self.source_key,
                ats_slug=company.slug,
            )


class TeamtailorCollector(AtsCollector):
    source_key = "teamtailor"
    source_name = "Teamtailor careers sites"
    _TT = "{https://teamtailor.com/locations}"
    _REMOTE = {"fully": "remote", "remote": "remote", "hybrid": "hybrid", "none": "onsite",
               "onsite": "onsite"}

    def feed_url(self, slug: str) -> str:
        return f"https://{slug.rstrip('/')}/jobs.rss"

    def fetch(self, client: httpx.Client, slug: str) -> Any:
        return fetch_xml(client, self.feed_url(slug))

    def parse(self, payload: ET.Element, company: TrackedCompany) -> Iterable[RawPosting]:
        for item in payload.iter("item"):
            places = []
            for loc in item.iter(f"{self._TT}location"):
                city = clean(loc.findtext(f"{self._TT}city")) or clean(loc.findtext(f"{self._TT}name"))
                country = clean(loc.findtext(f"{self._TT}country"))
                places.append(", ".join(p for p in (city, country) if p))
            location = "; ".join(dict.fromkeys(p for p in places if p)) or None
            remote_status = (item.findtext("remoteStatus") or "").strip().lower()
            published = item.findtext("pubDate")
            yield RawPosting(
                external_id=f"{company.slug}:{item.findtext('guid') or item.findtext('link')}",
                url=item.findtext("link"),
                title=clean(item.findtext("title")) or "(untitled)",
                company_name=company.name,
                location_text=location,
                work_mode=self._REMOTE.get(remote_status) or guess_work_mode(location),
                description=html_to_text(item.findtext("description")),
                posted_at=parsedate_to_datetime(published) if published else None,
                raw={"department": clean(item.findtext(f"{self._TT}department")),
                     "role": clean(item.findtext(f"{self._TT}role")), "remote_status": remote_status},
                ats_kind=self.source_key,
                ats_slug=company.slug,
            )


class RecruiteeCollector(AtsCollector):
    source_key = "recruitee"
    source_name = "Recruitee careers sites"
    _PERIODS = {"year": "year", "month": "month", "hour": "hour", "day": "day"}

    def feed_url(self, slug: str) -> str:
        return f"https://{slug}.recruitee.com/api/offers/"

    def parse(self, payload: Any, company: TrackedCompany) -> Iterable[RawPosting]:
        for offer in payload["offers"]:
            if offer.get("status", "published") != "published":
                continue
            if offer.get("hybrid"):
                work_mode = "hybrid"
            elif offer.get("remote"):
                work_mode = "remote"
            elif offer.get("on_site"):
                work_mode = "onsite"
            else:
                work_mode = "unknown"
            places = [
                ", ".join(p for p in (loc.get("city"), loc.get("country")) if p)
                for loc in offer.get("locations") or []
            ]
            location = "; ".join(dict.fromkeys(p for p in places if p)) or ", ".join(
                p for p in (offer.get("city"), offer.get("country")) if p
            ) or None
            salary = offer.get("salary") or {}
            low, high = _decimal(salary.get("min") or None), _decimal(salary.get("max") or None)
            description = "\n\n".join(
                t for t in (html_to_text(offer.get("description")), html_to_text(offer.get("requirements"))) if t
            )
            yield RawPosting(
                external_id=f"{company.slug}:{offer['id']}",
                url=offer.get("careers_url") or f"https://{company.slug}.recruitee.com/o/{offer['slug']}",
                title=clean(offer["title"]) or "(untitled)",
                company_name=company.name,
                location_text=location,
                work_mode=work_mode,
                description=description or None,
                contract_type=offer.get("employment_type_code"),
                salary_min=low,
                salary_max=high,
                salary_currency=salary.get("currency") if low or high else None,
                salary_period=self._PERIODS.get(salary.get("period") or "") if low or high else None,
                posted_at=from_iso((offer.get("published_at") or "").replace(" UTC", "+00:00") or None),
                raw={k: offer.get(k) for k in (
                    "experience_code", "employment_type_code", "category_code", "department",
                    "education_code", "remote", "hybrid", "on_site",
                )},
                ats_kind=self.source_key,
                ats_slug=company.slug,
            )


class TraffitCollector(AtsCollector):
    """Traffit, a Polish applicant tracking system. Its board fields are often in Polish.

    A job can be posted twice, in English and in Polish; both copies share a recruitment id,
    so only one is kept, the English one where there is one.
    """

    source_key = "traffit"
    source_name = "Traffit careers pages"

    def feed_url(self, slug: str) -> str:
        return f"https://{slug}.traffit.com/public/job_posts/published"

    def parse(self, payload: Any, company: TrackedCompany) -> Iterable[RawPosting]:
        chosen: dict[Any, dict] = {}
        for post in payload:
            recruitment = (post.get("advert") or {}).get("recruitment") or {}
            key = recruitment.get("id") or recruitment.get("nr_ref") or ("post", post["id"])
            if key not in chosen or (post.get("advert") or {}).get("language") == "en":
                chosen[key] = post
        for post in chosen.values():
            advert = post.get("advert") or {}
            values = {v.get("field_id"): v.get("value") for v in advert.get("values") or []}
            places = [
                ", ".join(p for p in (loc.get("locality"), loc.get("country")) if p)
                for loc in advert.get("locations") or []
            ]
            location = "; ".join(dict.fromkeys(p for p in places if p)) or None
            options = post.get("options") or {}
            remote = str(options.get("remote", "")) == "1"
            yield RawPosting(
                external_id=f"{company.slug}:{post['id']}",
                url=post["url"].split("?")[0],
                title=clean(advert.get("name")) or "(untitled)",
                company_name=company.name,
                location_text=location,
                work_mode="remote" if remote else guess_work_mode(location, advert.get("name")),
                description=html_to_text(values.get("description")),
                language=advert.get("language"),
                contract_type=options.get("job_type"),
                posted_at=from_iso((post.get("valid_start") or "").replace(" ", "T") or None),
                raw={"options": options, "nr_ref": (advert.get("recruitment") or {}).get("nr_ref")},
                ats_kind=self.source_key,
                ats_slug=company.slug,
            )


MORE_ATS_COLLECTORS = [
    PersonioCollector(),
    TeamtailorCollector(),
    RecruiteeCollector(),
    TraffitCollector(),
]
