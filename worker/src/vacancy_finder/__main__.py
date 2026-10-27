"""Worker entry point: migrate the database, then run all collectors on a fixed interval."""

import logging
import time

import psycopg

from .collectors import COLLECTORS
from .config import load_settings
from .migrate import apply_migrations
from .runner import run_all

log = logging.getLogger("vacancy_finder")


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    with psycopg.connect(settings.database_url) as conn:
        applied = apply_migrations(conn, settings.migrations_dir)
        log.info("Database ready (%d new migrations)", len(applied))

    while True:
        with psycopg.connect(settings.database_url) as conn:
            run_all(conn, COLLECTORS)
        log.info("Sleeping %d minutes", settings.run_interval_minutes)
        time.sleep(settings.run_interval_minutes * 60)


if __name__ == "__main__":
    main()
