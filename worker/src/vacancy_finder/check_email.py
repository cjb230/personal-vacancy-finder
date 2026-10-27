"""Reads the alerts inbox (read-only, nothing is marked read) and prints what the parsers find
in each message, without a database. Uses IMAP_USER / IMAP_PASSWORD etc. from the environment.

Usage: python -m vacancy_finder.check_email [--days 14] [--limit 20] [--save DIR]
--save writes each message to DIR as a .eml file, e.g. to build test fixtures.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .collectors.email_alerts import fetch_new, imap_settings_from_env, parse_message, parser_for


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--limit", type=int, default=20, help="newest N messages")
    parser.add_argument("--save", type=Path, help="folder to save messages as .eml")
    args = parser.parse_args()

    settings = imap_settings_from_env()
    if settings is None:
        sys.exit("Set IMAP_USER and IMAP_PASSWORD first (e.g. set -a; . ../.env; set +a)")
    since = datetime.now(UTC) - timedelta(days=args.days)
    _, messages = fetch_new(settings, lambda _: set(), since, limit=args.limit)
    print(f"{len(messages)} messages in {settings.folder} since {since:%Y-%m-%d}")
    if args.save:
        args.save.mkdir(parents=True, exist_ok=True)
    for msg in messages:
        alert = parser_for(msg.from_address)
        print(f"\n[{msg.uid}] {msg.received_at:%Y-%m-%d %H:%M} {msg.from_address} | {msg.subject!r}"
              f" | html={bool(msg.body_html)} | parser={alert.platform if alert else None}")
        if args.save and msg.raw:
            (args.save / f"{msg.uid}.eml").write_bytes(msg.raw)
        for p in parse_message(msg):
            print(f"  {p.title!r} | {p.company_name!r} | {p.location_text!r} | {p.work_mode} | {p.url}")


if __name__ == "__main__":
    main()
