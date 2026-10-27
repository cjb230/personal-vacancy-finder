import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    run_interval_minutes: int
    migrations_dir: Path
    log_level: str


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        run_interval_minutes=int(os.environ.get("RUN_INTERVAL_MINUTES", "360")),
        migrations_dir=Path(
            os.environ.get("MIGRATIONS_DIR", Path(__file__).resolve().parents[2] / "migrations")
        ),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
