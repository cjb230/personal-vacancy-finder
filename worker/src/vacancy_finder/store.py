"""Writes collected postings into the database, merging duplicates into one job."""

from dataclasses import asdict
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from .models import RawPosting
from .normalize import job_fingerprint, normalize_company


def ensure_source(conn: psycopg.Connection, key: str, name: str, kind: str) -> int:
    row = conn.execute(
        """
        INSERT INTO sources (key, name, kind) VALUES (%s, %s, %s)
        ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        (key, name, kind),
    ).fetchone()
    return row[0]


def ensure_company(conn: psycopg.Connection, posting: RawPosting) -> int:
    row = conn.execute(
        """
        INSERT INTO companies (name, normalized_name, ats_kind, ats_slug)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (normalized_name) DO UPDATE
            SET ats_kind = COALESCE(companies.ats_kind, EXCLUDED.ats_kind),
                ats_slug = COALESCE(companies.ats_slug, EXCLUDED.ats_slug)
        RETURNING id
        """,
        (
            posting.company_name,
            normalize_company(posting.company_name),
            posting.ats_kind,
            posting.ats_slug,
        ),
    ).fetchone()
    return row[0]


def ensure_job(conn: psycopg.Connection, company_id: int, posting: RawPosting) -> int:
    row = conn.execute(
        """
        INSERT INTO jobs (company_id, title, fingerprint, location_text, work_mode)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (fingerprint) DO UPDATE
            SET last_seen_at = now(),
                status = 'open',
                location_text = COALESCE(jobs.location_text, EXCLUDED.location_text),
                work_mode = CASE WHEN jobs.work_mode = 'unknown'
                                 THEN EXCLUDED.work_mode ELSE jobs.work_mode END
        RETURNING id
        """,
        (
            company_id,
            posting.title,
            job_fingerprint(posting.company_name, posting.title),
            posting.location_text,
            posting.work_mode,
        ),
    ).fetchone()
    return row[0]


def upsert_posting(conn: psycopg.Connection, source_id: int, posting: RawPosting) -> bool:
    """Store one posting. Returns True if it had not been seen before."""
    company_id = ensure_company(conn, posting)
    job_id = ensure_job(conn, company_id, posting)
    fields = asdict(posting)
    for unstored in ("ats_kind", "ats_slug"):
        fields.pop(unstored)
    fields["raw"] = Jsonb(fields["raw"])
    row = conn.execute(
        """
        INSERT INTO postings (
            source_id, job_id, external_id, url, title, company_name, location_text,
            work_mode, description, language, contract_type, salary_min, salary_max,
            salary_currency, salary_period, posted_at, raw
        ) VALUES (
            %(source_id)s, %(job_id)s, %(external_id)s, %(url)s, %(title)s, %(company_name)s,
            %(location_text)s, %(work_mode)s, %(description)s, %(language)s,
            %(contract_type)s, %(salary_min)s, %(salary_max)s, %(salary_currency)s,
            %(salary_period)s, %(posted_at)s, %(raw)s
        )
        ON CONFLICT (source_id, external_id) DO UPDATE SET
            job_id = EXCLUDED.job_id,
            url = EXCLUDED.url,
            title = EXCLUDED.title,
            location_text = EXCLUDED.location_text,
            work_mode = EXCLUDED.work_mode,
            description = EXCLUDED.description,
            contract_type = EXCLUDED.contract_type,
            salary_min = EXCLUDED.salary_min,
            salary_max = EXCLUDED.salary_max,
            salary_currency = EXCLUDED.salary_currency,
            salary_period = EXCLUDED.salary_period,
            raw = EXCLUDED.raw,
            last_seen_at = now(),
            closed_at = NULL
        RETURNING (xmax = 0) AS inserted
        """,
        {**fields, "source_id": source_id, "job_id": job_id},
    ).fetchone()
    return row[0]


def close_unseen(
    conn: psycopg.Connection,
    source_id: int,
    seen_since: datetime,
    skip_company_ids: set[int] = frozenset(),
) -> int:
    """Close this source's postings not seen since `seen_since`, then jobs with no open postings."""
    closed = conn.execute(
        """
        UPDATE postings p SET closed_at = now()
        FROM jobs j
        WHERE p.job_id = j.id
          AND p.source_id = %s AND p.closed_at IS NULL AND p.last_seen_at < %s
          AND (j.company_id IS NULL OR NOT j.company_id = ANY(%s))
        """,
        (source_id, seen_since, list(skip_company_ids)),
    ).rowcount
    conn.execute(
        """
        UPDATE jobs SET status = 'closed'
        WHERE status = 'open'
          AND NOT EXISTS (
              SELECT 1 FROM postings p WHERE p.job_id = jobs.id AND p.closed_at IS NULL
          )
        """
    )
    return closed
