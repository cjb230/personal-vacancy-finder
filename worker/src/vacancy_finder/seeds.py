"""Loads the hand-kept company list (seeds/companies.csv) into the companies table."""

import csv
import logging
from pathlib import Path

import psycopg

from .normalize import normalize_company

log = logging.getLogger(__name__)

COLUMNS = ["name", "sectors", "ats_kind", "ats_slug", "website", "notes"]


def load_companies(conn: psycopg.Connection, path: Path) -> int:
    """Upsert every row. The CSV wins for the fields it sets, so edits to it take effect."""
    if not path.exists():
        log.info("No company seed file at %s", path)
        return 0
    with path.open(newline="", encoding="utf-8") as f:
        rows = [row for row in csv.DictReader(f) if row["name"].strip()]
    for row in rows:
        ats_kind = row["ats_kind"].strip() or None
        ats_slug = row["ats_slug"].strip() or None
        conn.execute(
            """
            INSERT INTO companies
                (name, normalized_name, sectors, ats_kind, ats_slug, website, notes, tracked)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (normalized_name) DO UPDATE SET
                name = EXCLUDED.name,
                sectors = EXCLUDED.sectors,
                ats_kind = COALESCE(EXCLUDED.ats_kind, companies.ats_kind),
                ats_slug = COALESCE(EXCLUDED.ats_slug, companies.ats_slug),
                website = COALESCE(EXCLUDED.website, companies.website),
                notes = COALESCE(EXCLUDED.notes, companies.notes),
                tracked = EXCLUDED.tracked OR companies.tracked
            """,
            (
                row["name"].strip(),
                normalize_company(row["name"]),
                [s.strip() for s in row["sectors"].split(";") if s.strip()],
                ats_kind,
                ats_slug,
                row["website"].strip() or None,
                row["notes"].strip() or None,
                bool(ats_kind and ats_slug),
            ),
        )
    conn.commit()
    log.info("Loaded %d companies from %s", len(rows), path.name)
    return len(rows)
