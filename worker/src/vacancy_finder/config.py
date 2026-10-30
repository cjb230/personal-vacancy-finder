import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    email_interval_minutes: int
    board_interval_minutes: int
    ats_interval_minutes: int
    migrations_dir: Path
    seeds_file: Path
    log_level: str


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        email_interval_minutes=int(os.environ.get("EMAIL_INTERVAL_MINUTES", "15")),
        board_interval_minutes=int(os.environ.get("BOARD_INTERVAL_MINUTES", "120")),
        ats_interval_minutes=int(os.environ.get("ATS_INTERVAL_MINUTES", "360")),
        migrations_dir=Path(
            os.environ.get("MIGRATIONS_DIR", Path(__file__).resolve().parents[2] / "migrations")
        ),
        seeds_file=Path(
            os.environ.get(
                "SEEDS_FILE", Path(__file__).resolve().parents[2] / "seeds" / "companies.csv"
            )
        ),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
