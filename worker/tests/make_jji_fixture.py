"""Builds tests/fixtures/justjoinit_offer.html, shaped like a real justjoin.it advert page
(as observed 2026-10-27): a JobPosting JSON-LD block plus the offer object inside a
self.__next_f.push payload, preceded by a similar-offers entry for another slug."""

import json
from functools import partial
from pathlib import Path

compact = partial(json.dumps, separators=(",", ":"))

SLUG = "shelf-senior-backend-python-engineer-warszawa-python-a3b545e2"

job_posting = {
    "@context": "https://schema.org", "@type": "JobPosting",
    "datePosted": "2026-10-25T08:05:06.4602632Z",
    "description": "<p>About Shelf</p><p>The enterprise is going agentic and we are building the platform for it.</p>",
    "title": "Senior Backend Python Engineer", "validThrough": "2026-11-26T22:59:59.999999Z",
    "employmentType": "FULL_TIME",
    "baseSalary": {"@type": "MonetaryAmount", "currency": "PLN", "value": {
        "@type": "QuantitativeValue", "unitText": "MONTH", "minValue": 31292.8, "maxValue": 46939.2}},
    "hiringOrganization": {"@type": "Organization", "name": "Shelf"},
    "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressCountry": "PL",
                                                  "addressLocality": "Warszawa"}},
}

def salary(currency, lo, hi, source, kind="b2b", gross=False):
    return {"currency": currency, "from": lo, "to": hi, "gross": gross, "type": kind,
            "unit": "month", "label": kind, "currencySource": source}

offer = {
    "slug": SLUG, "title": "Senior Backend Python Engineer",
    "experienceLevel": {"label": "senior", "value": "senior"},
    "category": {"key": "python", "parentKey": None}, "companyName": "Shelf", "body": "$81",
    "city": "Warszawa", "street": "Prosta 20",
    "employmentTypes": [
        salary("EUR", 7300.1, 10950.2, "conversion"),
        salary("PLN", 31292.8, 46939.2, "conversion"),
        salary("USD", 8000, 12000, "original"),
    ],
    "workplaceType": {"label": "office", "value": "office"},
    "requiredSkills": [{"id": "Python", "name": "Python", "level": 4}],
    "workingTime": {"label": "full_time", "value": "full_time"},
    "applyUrl": "https://jobs.ashbyhq.com/shelf?utm_source=jZgzoGzpkQ",
    "publishedAt": "2026-10-25T08:05:06.4602632Z",
    "multilocation": [{"slug": SLUG, "city": "Warszawa"}],
    "isOfferActive": True, "countryCode": "PL", "languages": [{"code": "en", "level": "B2"}],
    "guid": "015c71ad-b203-4cd6-8a7a-d40962154c32",
}

similar = {"slug": "acme-python-developer-krakow-python-ffff0000", "title": "Python Developer"}
payload = (
    "3:[\"$\",\"section\",null,{\"similar\":[" + compact(similar) + "]}]\n"
    "81:T40,<p>About Shelf</p>\n"
    "7:[\"$\",\"$L1\",null,{\"offer\":" + compact(offer) + "}]\n"
)

page = f"""<!DOCTYPE html><html><head>
<script type="application/ld+json">{json.dumps({"@context": "https://schema.org", "@type": "BreadcrumbList"})}</script>
<script type="application/ld+json">{json.dumps(job_posting)}</script>
</head><body><div>Senior Backend Python Engineer</div>
<script>self.__next_f.push([1,"1:I[123,[],\\"default\\"]\\n"])</script>
<script>self.__next_f.push([1,{json.dumps(payload)}])</script>
</body></html>"""

Path(__file__).with_name("fixtures").joinpath("justjoinit_offer.html").write_text(page)
