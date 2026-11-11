"""Well-known employers whose jobs get a small bonus when scored (matching.BIG_NAME_BONUS).

The list is seeds/big_names.csv, next to the company seed file, so in Docker it is the mounted
seeds folder and edits apply at the next scoring without a rebuild. Columns: name, aliases
(separated by ";"). A company matches when its normalised name (normalize_company: case,
spaces and legal suffixes ignored, "Google Poland" = "Google") equals the name or an alias.
Matching is exact so that "Apple Leisure Group" isn't Apple; list other spellings as aliases.

To see which companies in the database match, and which look close but don't:
    python -m vacancy_finder.big_names --check
"""

import argparse
import csv
import logging
import os
from pathlib import Path

import psycopg

from .normalize import normalize_company

log = logging.getLogger(__name__)


def path() -> Path:
    seeds = os.environ.get("SEEDS_FILE")
    if seeds:
        return Path(seeds).with_name("big_names.csv")
    return Path(__file__).resolve().parents[2] / "seeds" / "big_names.csv"


def load(file: Path | None = None) -> dict[str, str]:
    """Normalised name or alias -> the listed name. Empty if there is no file."""
    file = file or path()
    if not file.exists():
        return {}
    keys: dict[str, str] = {}
    with file.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            if not name:
                continue
            for spelling in [name, *(row.get("aliases") or "").split(";")]:
                if key := normalize_company(spelling.strip()):
                    keys[key] = name
    return keys


def check(conn: psycopg.Connection, keys: dict[str, str]) -> None:
    """Print the companies with open jobs that match, and those whose name starts like a
    listed one without matching (candidates for an alias, or rightly left out)."""
    rows = conn.execute(
        """
        SELECT c.name, count(*) FROM companies c JOIN jobs j ON j.company_id = c.id
        WHERE j.status = 'open' GROUP BY c.name ORDER BY c.name
        """
    ).fetchall()
    matched, close = [], []
    for name, jobs in rows:
        key = normalize_company(name)
        if key in keys:
            matched.append(f"  {name} ({jobs} open jobs) = {keys[key]}")
        elif any(key.startswith(k) for k in keys):
            close.append(f"  {name} ({jobs} open jobs)")
    print(f"{len(keys)} spellings of {len(set(keys.values()))} names in {path()}.")
    print("Matching:", *matched or ["  (none)"], sep="\n")
    print("Starting like a listed name but not matching (add as an alias if it's the same "
          "company):", *close or ["  (none)"], sep="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="list the companies in the database that match, and near misses")
    args = parser.parse_args()
    if not args.check:
        parser.print_help()
        return
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        check(conn, load())


if __name__ == "__main__":
    main()
