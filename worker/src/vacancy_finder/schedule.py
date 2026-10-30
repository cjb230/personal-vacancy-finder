"""When each source is next due.

Each source keeps its own clock: it is due once its interval has passed since its last run
started, as recorded in collection_runs. So a restart does not rerun everything, and sources
drift apart over time instead of all firing together.
"""

from datetime import datetime, timedelta

import psycopg

from .collectors import Collector
from .config import Settings


def interval_for(collector: Collector, settings: Settings) -> timedelta:
    """Email alerts are cheap and time-sensitive, job boards move faster than careers pages."""
    match collector.source_kind:
        case "email":
            minutes = settings.email_interval_minutes
        case "board" | "aggregator":
            minutes = settings.board_interval_minutes
        case _:
            minutes = settings.ats_interval_minutes
    return timedelta(minutes=minutes)


def last_started(conn: psycopg.Connection) -> dict[str, datetime]:
    """When each source's latest run started, by source key."""
    return dict(
        conn.execute(
            """
            SELECT s.key, max(r.started_at)
            FROM collection_runs r JOIN sources s ON s.id = r.source_id
            GROUP BY s.key
            """
        ).fetchall()
    )


def due_times(
    collectors: list[Collector], settings: Settings, last: dict[str, datetime], now: datetime
) -> dict[str, datetime]:
    """When each collector is next due; a source that has never run is due now."""
    return {
        c.source_key: (
            last[c.source_key] + interval_for(c, settings) if c.source_key in last else now
        )
        for c in collectors
    }


def due_now(
    collectors: list[Collector], settings: Settings, last: dict[str, datetime], now: datetime
) -> list[Collector]:
    """The collectors due now, shortest interval first so a long careers-page pass does not
    hold up the email check; ties keep the given order."""
    due = due_times(collectors, settings, last, now)
    ready = [c for c in collectors if due[c.source_key] <= now]
    return sorted(ready, key=lambda c: interval_for(c, settings))
