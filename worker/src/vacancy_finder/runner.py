"""Runs each collector once and records how it went."""

import logging
from datetime import timedelta

import psycopg

from .collectors import Collector
from .store import (
    close_expired_unseen,
    close_postings_by_url,
    close_unseen,
    confirm_tracked_jobs,
    ensure_source,
    missing_tracked_urls,
    upsert_posting,
)

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
        # Feeds that only show the latest N jobs close a posting after it has been
        # missing for a while, rather than on its first absence.
        stale_days = getattr(collector, "stale_after_days", 0)
        if stale_days:
            # Absence from such a feed proves nothing, but a passed expiry date does, and
            # tracked jobs' advert pages can be checked one by one where the collector can.
            close_expired_unseen(conn, source_id, started_at)
            if hasattr(collector, "gone_adverts") and (
                    missing := missing_tracked_urls(conn, source_id, started_at)):
                close_postings_by_url(conn, source_id, collector.gone_adverts(missing))
        closed = close_unseen(
            conn,
            source_id,
            started_at - timedelta(days=stale_days),
            skip_company_ids=collector.incomplete_company_ids(),
        )
        # Interested and applied jobs whose adverts have now all gone are flagged.
        gone, back = confirm_tracked_jobs(conn)
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
        if gone or back:
            log.info("%s: %d tracked jobs' adverts gone, %d back", collector.source_key, gone, back)
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

