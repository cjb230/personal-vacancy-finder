"""Finds companies' careers feeds in the jobs already collected from boards and aggregators.

Adverts on Hacker News, Himalayas, Arbeitnow and the other boards often link to the company's
own Greenhouse, Lever, Ashby or other ATS board. Each board found that isn't tracked yet is a
company whose whole feed could be collected, so this lists them, best-scoring jobs first.

Usage: python -m vacancy_finder.discover [--all] [--min-postings N]
Prints seed-file rows for the untracked boards, ready to check and paste into seeds/companies.csv.
"""

import argparse
import csv
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import unquote

import psycopg

# (ats_kind, pattern); group 1 becomes the ats_slug.
BOARD_PATTERNS = [
    ("greenhouse", re.compile(
        r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_app\?(?:[^\"'\s]*&)?for=([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"//jobs\.lever\.co/([A-Za-z0-9_.-]+)")),
    ("lever-eu", re.compile(r"//jobs\.eu\.lever\.co/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"//([a-z0-9-]+)\.workable\.com", re.I)),
    ("personio", re.compile(r"([a-z0-9-]+\.jobs\.personio\.(?:de|com))", re.I)),
    ("recruitee", re.compile(r"//([a-z0-9-]+)\.recruitee\.com", re.I)),
    ("traffit", re.compile(r"//([a-z0-9-]+)\.traffit\.com", re.I)),
    ("teamtailor", re.compile(r"//([a-z0-9-]+\.teamtailor\.com)", re.I)),
]

# Path parts and subdomains that belong to the ATS itself, not to a company.
NOT_SLUGS = {"api", "app", "apply", "embed", "j", "jobs", "www", "careers", "help", "support", "static"}


def find_boards(text: str | None) -> set[tuple[str, str]]:
    """Every (ats_kind, ats_slug) a piece of text links to."""
    found = set()
    for kind, pattern in BOARD_PATTERNS:
        for match in pattern.finditer(text or ""):
            slug = unquote(match.group(1)).strip(".-")
            if kind in ("personio", "teamtailor", "workable", "recruitee", "traffit"):
                slug = slug.lower()
            if slug and slug.split(".")[0].lower() not in NOT_SLUGS:
                found.add((kind, slug))
    return found


@dataclass
class Board:
    kind: str
    slug: str
    postings: int = 0
    verdicts: Counter = field(default_factory=Counter)
    companies: Counter = field(default_factory=Counter)
    titles: list[str] = field(default_factory=list)


def scan(conn: psycopg.Connection) -> dict[tuple[str, str], Board]:
    """Board links in every open posting from a source that isn't itself an ATS feed."""
    rows = conn.execute(
        """
        SELECT p.url, p.description, p.raw::text, p.company_name, p.title, s.verdict
        FROM postings p
        JOIN sources src ON src.id = p.source_id
        LEFT JOIN job_scores s ON s.job_id = p.job_id
        WHERE src.kind <> 'ats' AND p.closed_at IS NULL
        """
    )
    boards: dict[tuple[str, str], Board] = {}
    for url, description, raw, company, title, verdict in rows:
        for kind, slug in find_boards(" ".join(filter(None, (url, description, raw)))):
            board = boards.setdefault((kind, slug.lower()), Board(kind, slug))
            board.postings += 1
            board.verdicts[verdict or "unscored"] += 1
            board.companies[company] += 1
            if len(board.titles) < 3:
                board.titles.append(title)
    return boards


def tracked_boards(conn: psycopg.Connection) -> set[tuple[str, str]]:
    rows = conn.execute("SELECT ats_kind, lower(ats_slug) FROM companies WHERE ats_slug IS NOT NULL")
    return {(kind, slug) for kind, slug in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="include boards already in the company list")
    parser.add_argument("--min-postings", type=int, default=1)
    args = parser.parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        boards = scan(conn)
        known = tracked_boards(conn)

    found = [b for key, b in boards.items()
             if b.postings >= args.min_postings and (args.all or key not in known)]
    found.sort(key=lambda b: (-b.verdicts["match"], -b.verdicts["maybe"], -b.postings, b.slug))

    out = csv.writer(sys.stdout)
    for b in found:
        name = b.companies.most_common(1)[0][0]
        print(f"# {name}: {b.kind}/{b.slug}, {b.postings} postings "
              f"({b.verdicts['match']} match, {b.verdicts['maybe']} maybe), e.g. {b.titles[0]!r}",
              file=sys.stderr)
        out.writerow([name, "", b.kind, b.slug, "", "found in collected adverts", "", ""])
    print(f"# {len(found)} boards", file=sys.stderr)


if __name__ == "__main__":
    main()
