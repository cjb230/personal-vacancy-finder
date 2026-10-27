"""Finds which ATS a company uses by trying likely slugs on each public feed.

Usage: python -m vacancy_finder.probe "KP Labs" "ICEYE" ...
       python -m vacancy_finder.probe --file seeds/candidates.txt
Prints seed-file rows for every hit, ready to paste into seeds/companies.csv.
"""

import argparse
import csv
import re
import sys

import httpx

from .collectors.ats import ATS_COLLECTORS, USER_AGENT, TrackedCompany
from .normalize import normalize_company


def candidate_slugs(name: str) -> list[str]:
    words = normalize_company(name).split()
    full = re.findall(r"[a-z0-9]+", name.lower())
    variants = ["".join(words), "-".join(words), "".join(full), "-".join(full)]
    # A distinctive first word alone, e.g. "iceye" for "ICEYE Polska".
    if len(words) > 1 and len(words[0]) >= 5:
        variants.append(words[0])
    seen: list[str] = []
    for v in variants:
        v = re.sub(r"[^a-z0-9_-]", "", v)
        if v and v not in seen:
            seen.append(v)
    return seen


def count_jobs(payload) -> int:
    if isinstance(payload, list):
        return len(payload)
    return len(payload.get("jobs", []))


def check_parse(collector, payload, name: str, slug: str) -> str:
    """Run the collector's parser on the live feed, to catch format surprises early."""
    try:
        postings = list(collector.parse(payload, TrackedCompany(0, name, slug)))
    except Exception as exc:  # report, don't stop the probe
        return f"PARSE FAILED: {exc!r}"
    if not postings:
        return "parsed 0"
    p = postings[0]
    return f"parsed {len(postings)}, e.g. {p.title!r} | {p.location_text!r} | {p.work_mode}"


def probe(client: httpx.Client, name: str, extra_slugs: list[str]) -> list[tuple[str, str, int, str]]:
    hits = []
    for collector in ATS_COLLECTORS:
        for slug in extra_slugs + candidate_slugs(name):
            try:
                payload = collector.fetch(client, slug)
            except (httpx.HTTPError, ValueError):
                continue
            if isinstance(payload, dict) and "jobs" not in payload:
                continue
            # Workable and Ashby answer made-up slugs with an empty board rather than a 404,
            # so an empty board is not evidence of a match.
            if count_jobs(payload) == 0:
                continue
            hits.append(
                (collector.source_key, slug, count_jobs(payload),
                 check_parse(collector, payload, name, slug))
            )
            break
    return hits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="company names; add slugs to try as NAME=slug1,slug2")
    parser.add_argument("--file", help="read names from a file, one per line, # for comments")
    args = parser.parse_args()
    names = list(args.names)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            names += [line.strip() for line in f if line.strip() and not line.startswith("#")]

    out = csv.writer(sys.stdout)
    with httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        for arg in names:
            name, _, extra = arg.partition("=")
            hits = probe(client, name, [s for s in extra.split(",") if s])
            if not hits:
                print(f"# {name}: no ATS feed found", file=sys.stderr)
            for kind, slug, jobs, check in hits:
                print(f"# {name}: {kind}/{slug} has {jobs} open jobs; {check}", file=sys.stderr)
                out.writerow([name, "", kind, slug, "", ""])


if __name__ == "__main__":
    main()
