"""Reads the alerts inbox read-only (nothing is marked read, moved or deleted) and prints what
the parsers find in each alert, without a database. Uses IMAP_USER / IMAP_PASSWORD etc. from
the environment.

Usage: python -m vacancy_finder.check_email [--days 14] [--limit 20] [--save DIR]
--save writes each alert to DIR as a .eml file, e.g. to build test fixtures.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .collectors.email_alerts import Inbox, imap_settings_from_env, parse_message, parser_for


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--limit", type=int, default=20, help="newest N alerts")
    parser.add_argument("--save", type=Path, help="folder to save alerts as .eml")
    args = parser.parse_args()

    settings = imap_settings_from_env()
    if settings is None:
        sys.exit("Set IMAP_USER and IMAP_PASSWORD first (e.g. set -a; . ../.env; set +a)")
    since = datetime.now(UTC) - timedelta(days=args.days)
    if args.save:
        args.save.mkdir(parents=True, exist_ok=True)
    with Inbox(settings) as inbox:
        senders = inbox.senders(inbox.uids(since))
        alerts = [uid for uid, sender in senders.items() if parser_for(sender)]
        print(f"{len(senders)} messages in {settings.folder} since {since:%Y-%m-%d}, "
              f"{len(alerts)} from alert senders; showing the newest {args.limit}")
        for uid in sorted(alerts)[-args.limit:]:
            msg = inbox.fetch(uid)
            if msg is None:
                continue
            postings = parse_message(msg)
            verdict = "would be removed" if postings else "would stay in the inbox"
            print(f"\n[{uid}] {msg.received_at:%Y-%m-%d %H:%M} {msg.from_address} | "
                  f"{msg.subject!r} | {parser_for(msg.from_address).platform} | "
                  f"{len(postings)} jobs, {verdict}")
            if args.save and msg.raw:
                (args.save / f"{uid}.eml").write_bytes(msg.raw)
            for p in postings:
                print(f"  {p.title!r} | {p.company_name!r} | {p.location_text!r} | "
                      f"{p.work_mode} | {p.url}")


if __name__ == "__main__":
    main()
