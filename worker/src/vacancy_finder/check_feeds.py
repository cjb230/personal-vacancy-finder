"""Runs the remote-board collectors against the live sites and prints a summary,
without touching the database.

Usage: python -m vacancy_finder.check_feeds [source_key ...]
"""

import json
import sys
from datetime import datetime
from decimal import Decimal

from .collectors.remote import REMOTE_COLLECTORS


def main(keys: list[str]) -> None:
    for collector in REMOTE_COLLECTORS:
        if keys and collector.source_key not in keys:
            continue
        try:
            postings = list(collector.collect(None))
        except Exception as exc:  # report and carry on with the next source
            print(f"{collector.source_key}: FAILED {exc!r}")
            continue
        print(f"{collector.source_key}: {len(postings)} postings")
        for p in postings[:2]:
            print("  " + json.dumps({
                k: str(v) if isinstance(v, (Decimal, datetime)) else v
                for k, v in p.__dict__.items() if k not in ("raw", "description", "ats_kind", "ats_slug")
            }, ensure_ascii=False))
            print("  description: " + repr((p.description or "")[:160]))


if __name__ == "__main__":
    main(sys.argv[1:])
