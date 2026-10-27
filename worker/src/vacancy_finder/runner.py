"""Runs each collector once and records how it went."""

import logging

import psycopg

from .collectors import Collector
from .store import close_unseen, ensure_source, upsert_posting

log = logging.getLogger(__name__)


def run_collector(conn: psycopg.Connection, collector: Collector) -> None:
    source_id = ensure_source(
        conn, collector.source_key, collector.source_name, collector.source_kind
    )
    run_id, started_at = conn.execute(
        "INSERT INTO collection_runs (source_id) VALUES (%s) RETURNING id, started_at",
        (source_id,),
    ).fetchone()
    conn.commit()

    seen = new = 0
    try:
        for posting in collector.collect(conn):
            seen += 1
            if upsert_posting(conn, source_id, posting):
                new += 1
        # Only close what is missing after a successful pass, and not for companies that failed.
        closed = close_unseen(
            conn, source_id, started_at, skip_company_ids=collector.incomplete_company_ids()
        )
        conn.execute(
            """
            UPDATE collection_runs
            SET status = 'ok', finished_at = now(), postings_seen = %s, postings_new = %s
            WHERE id = %s
            """,
            (seen, new, run_id),
        )
        conn.commit()
        log.info("%s: %d seen, %d new, %d closed", collector.source_key, seen, new, closed)
    except Exception as exc:
        conn.rollback()
        conn.execute(
            """
            UPDATE collection_runs SET status = 'failed', finished_at = now(), error = %s
            WHERE id = %s
            """,
            (repr(exc), run_id),
        )
        conn.commit()
        log.exception("%s failed", collector.source_key)


def run_all(conn: psycopg.Connection, collectors: list[Collector]) -> None:
    if not collectors:
        log.info("No collectors registered yet")
    for collector in collectors:
        run_collector(conn, collector)
