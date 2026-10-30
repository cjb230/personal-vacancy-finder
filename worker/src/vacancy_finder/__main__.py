"""Worker entry point: migrate the database, serve the results page, then run each collector
on its own interval and rescore the jobs after each round."""

import logging
import time
from datetime import timedelta

import psycopg

from .collectors import COLLECTORS
from .config import load_settings
from .matching import score_all
from .migrate import apply_migrations
from .renormalize import renormalize
from .runner import run_collector
from .schedule import due_now, due_times, last_started
from .seeds import load_companies
from .store import fill_deadlines
from .web import serve

log = logging.getLogger("vacancy_finder")


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    with psycopg.connect(settings.database_url) as conn:
        applied = apply_migrations(conn, settings.migrations_dir)
        log.info("Database ready (%d new migrations)", len(applied))
        renormalize(conn)  # in case the company/job matching rules changed
        fill_deadlines(conn)  # in case the deadline wording rules changed
        score_all(conn)  # in case the scoring rules changed
    serve()

    if not COLLECTORS:
        log.info("No collectors registered yet")
    while True:
        with psycopg.connect(settings.database_url) as conn:
            # The database clock, since collection_runs.started_at is set by it.
            now = conn.execute("SELECT now()").fetchone()[0]
            due = due_now(COLLECTORS, settings, last_started(conn), now)
            if due:
                # Re-read each round so edits to the seed file apply without a restart.
                load_companies(conn, settings.seeds_file)
                for collector in due:
                    run_collector(conn, collector)
                score_all(conn)
            now = conn.execute("SELECT now()").fetchone()[0]
            next_due = min(due_times(COLLECTORS, settings, last_started(conn), now).values(),
                           default=now + timedelta(hours=1))
        wait = max((next_due - now).total_seconds(), 30)
        log.info("Next source due in %d minutes", round(wait / 60))
        time.sleep(wait)


if __name__ == "__main__":
    main()
