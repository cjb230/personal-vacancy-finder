"""Re-keys stored companies and jobs after the matching rules in normalize.py change, merging
rows that now count as the same company or the same job. It does nothing when the keys are
already current, so the worker runs it on every start.
"""

import logging
from collections import defaultdict

import psycopg

from .normalize import PLACEHOLDER_TITLE, UNKNOWN_COMPANY, job_fingerprint, normalize_company

log = logging.getLogger(__name__)

_COMPANY_FIELDS = ["ats_kind", "ats_slug", "careers_url", "link_pattern", "website", "notes",
                   "size_hint", "exclusion_reason"]


def renormalize(conn: psycopg.Connection) -> tuple[int, int]:
    """Returns (companies merged, jobs merged)."""
    with conn.transaction():
        companies = _rekey_companies(conn)
        jobs = _rekey_jobs(conn)
    if companies or jobs:
        log.info("Re-keyed stored data: merged %d companies and %d jobs", companies, jobs)
    return companies, jobs


def _rekey_companies(conn: psycopg.Connection) -> int:
    rows = conn.execute("SELECT id, name, normalized_name FROM companies ORDER BY id").fetchall()
    if all(normalize_company(name) == key for _, name, key in rows):
        return 0
    groups: dict[str, list[int]] = defaultdict(list)
    for company_id, name, _ in rows:
        groups[normalize_company(name)].append(company_id)
    names = {company_id: name for company_id, name, _ in rows}

    merged = 0
    for ids in groups.values():
        keep, *others = ids
        if others:
            _absorb_companies(conn, keep, others)
            # The plainest spelling: "Capgemini" over "Capgemini Polska".
            name = min((names[i] for i in ids), key=len)
            conn.execute("UPDATE companies SET name = %s WHERE id = %s", (name, keep))
            merged += len(others)
    # Two passes, so no row takes a key that another row still holds.
    conn.execute("UPDATE companies SET normalized_name = 'tmp:' || id")
    for key, (keep, *_) in groups.items():
        conn.execute("UPDATE companies SET normalized_name = %s WHERE id = %s", (key, keep))
    return merged


def _absorb_companies(conn: psycopg.Connection, keep: int, others: list[int]) -> None:
    """Move the others' jobs to `keep`, fill its blanks from them, then delete them."""
    cols = ", ".join(_COMPANY_FIELDS)
    kept = conn.execute(
        f"SELECT {cols}, sectors, tracked, excluded FROM companies WHERE id = %s", (keep,)
    ).fetchone()
    values = dict(zip([*_COMPANY_FIELDS, "sectors", "tracked", "excluded"], kept))
    # Tracked rows first, so a seeded feed wins over one learnt from a posting.
    for row in conn.execute(
        f"SELECT {cols}, sectors, tracked, excluded FROM companies WHERE id = ANY(%s) "
        "ORDER BY tracked DESC, id",
        (others,),
    ).fetchall():
        other = dict(zip([*_COMPANY_FIELDS, "sectors", "tracked", "excluded"], row))
        for field in _COMPANY_FIELDS:
            values[field] = values[field] or other[field]
        values["sectors"] = list(dict.fromkeys(values["sectors"] + other["sectors"]))
        values["tracked"] |= other["tracked"]
        values["excluded"] |= other["excluded"]
    if not (values["ats_kind"] and values["ats_slug"]):
        values["ats_kind"] = values["ats_slug"] = None

    conn.execute("UPDATE jobs SET company_id = %s WHERE company_id = ANY(%s)", (keep, others))
    conn.execute("DELETE FROM companies WHERE id = ANY(%s)", (others,))
    sets = ", ".join(f"{field} = %({field})s" for field in values)
    conn.execute(f"UPDATE companies SET {sets} WHERE id = %(id)s", {**values, "id": keep})


def _rekey_jobs(conn: psycopg.Connection) -> int:
    """Recompute each job's fingerprint from its company and title, and merge jobs that now
    share one. Jobs made one-per-posting (no usable company or title) keep their own key."""
    rows = conn.execute(
        """
        SELECT j.id, j.fingerprint, c.name, j.title FROM jobs j
        LEFT JOIN companies c ON c.id = j.company_id ORDER BY j.id
        """
    ).fetchall()
    groups: dict[str, list[int]] = defaultdict(list)
    for job_id, fingerprint, company, title in rows:
        if (company is None or title == PLACEHOLDER_TITLE
                or normalize_company(company) in ("", UNKNOWN_COMPANY)):
            key = fingerprint  # one job per posting; leave as is
        else:
            key = job_fingerprint(company, title)
        groups[key].append(job_id)
    current = {job_id: fingerprint for job_id, fingerprint, *_ in rows}
    if all(ids == [ids[0]] and current[ids[0]] == key for key, ids in groups.items()):
        return 0

    merged = 0
    for ids in groups.values():
        keep, *others = ids
        if not others:
            continue
        conn.execute("UPDATE postings SET job_id = %s WHERE job_id = ANY(%s)", (keep, others))
        conn.execute(
            """
            UPDATE jobs k SET
                first_seen_at = LEAST(k.first_seen_at, o.first_seen_at),
                last_seen_at = GREATEST(k.last_seen_at, o.last_seen_at),
                status = CASE WHEN k.status = 'open' OR o.any_open THEN 'open' ELSE 'closed' END,
                location_text = COALESCE(k.location_text, o.location_text),
                work_mode = CASE WHEN k.work_mode = 'unknown' THEN o.work_mode ELSE k.work_mode END,
                excluded = k.excluded OR o.any_excluded
            FROM (
                SELECT min(first_seen_at) AS first_seen_at, max(last_seen_at) AS last_seen_at,
                       bool_or(status = 'open') AS any_open, bool_or(excluded) AS any_excluded,
                       max(location_text) AS location_text,
                       COALESCE(max(work_mode) FILTER (WHERE work_mode <> 'unknown'), 'unknown')
                           AS work_mode
                FROM jobs WHERE id = ANY(%(others)s)
            ) o
            WHERE k.id = %(keep)s
            """,
            {"keep": keep, "others": others},
        )
        conn.execute("DELETE FROM jobs WHERE id = ANY(%s)", (others,))
        merged += len(others)
    conn.execute("UPDATE jobs SET fingerprint = 'tmp:' || id")
    for key, (keep, *_) in groups.items():
        conn.execute("UPDATE jobs SET fingerprint = %s WHERE id = %s", (key, keep))
    return merged
