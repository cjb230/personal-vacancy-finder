"""Fetches and parses the careers feed of each company in the seed file, without a database,
and prints what it found. Use it after adding rows to seeds/companies.csv.

Usage: python -m vacancy_finder.check_companies [--kind page] [--file seeds/companies.csv] [NAME ...]
"""

import argparse
import csv
import time

import httpx

from .collectors import COLLECTORS
from .collectors.ats import USER_AGENT, AtsCollector, TrackedCompany


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="only these companies")
    parser.add_argument("--kind", action="append", help="only this ats_kind (repeatable)")
    parser.add_argument("--file", default="seeds/companies.csv")
    parser.add_argument("--delay", type=float, default=2.0, help="seconds between requests")
    args = parser.parse_args()

    by_kind = {c.source_key: c for c in COLLECTORS if isinstance(c, AtsCollector)}
    with open(args.file, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["ats_kind"] and r["ats_slug"]]
    rows = [r for r in rows if (not args.names or r["name"] in args.names)
            and (not args.kind or r["ats_kind"] in args.kind)]

    with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        for i, row in enumerate(rows):
            if i:
                time.sleep(args.delay)
            collector = by_kind.get(row["ats_kind"])
            if collector is None:
                print(f"{row['name']}: no collector for {row['ats_kind']!r}")
                continue
            company = TrackedCompany(0, row["name"], row["ats_slug"], row.get("careers_url") or None,
                                     row.get("link_pattern") or None)
            try:
                postings = list(collector.parse(collector.fetch_company(client, company), company))
            except Exception as exc:  # report and carry on with the next company
                print(f"{row['name']} ({row['ats_kind']}): FAILED {exc!r}")
                continue
            print(f"{row['name']} ({row['ats_kind']}): {len(postings)} postings")
            for p in postings[:3]:
                print(f"  {p.title!r} | {p.location_text!r} | {p.work_mode} | {p.url}")
                if p.description:
                    print(f"    {p.description[:120]!r}")


if __name__ == "__main__":
    main()
