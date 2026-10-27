"""Fetches a few matching Just Join IT adverts and prints what the parser makes of them.

Usage: python -m vacancy_finder.check_justjoinit [N]
"""

import json
import sys
import time
from datetime import datetime
from decimal import Decimal

import httpx

from .collectors.ats import USER_AGENT
from .collectors.justjoinit import JustJoinItCollector, parse_offer_page


def sample(n: int) -> None:
    collector = JustJoinItCollector()
    with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        entries = [e for e in collector.sitemap_entries(client) if collector.wanted(e)]
        print(f"{len(entries)} matching adverts in the sitemap")
        for i, entry in enumerate(entries[:n]):
            if i:
                time.sleep(collector.delay)
            posting = parse_offer_page(client.get(entry.url).text, entry)
            if posting is None:
                print(f"NO DATA: {entry.url}")
                continue
            print(json.dumps({
                k: str(v) if isinstance(v, (Decimal, datetime)) else v
                for k, v in posting.__dict__.items() if k not in ("raw", "description")
            }, ensure_ascii=False))


if __name__ == "__main__":
    sample(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
