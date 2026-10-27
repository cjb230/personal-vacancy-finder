from collections.abc import Iterable
from typing import Protocol

import psycopg

from ..models import RawPosting


class Collector(Protocol):
    """A source of postings. One collector covers one source key."""

    source_key: str  # matches sources.key
    source_name: str
    source_kind: str  # ats | board | aggregator | email | custom
    # Optional: for feeds that list only recent jobs, close a posting once it has been
    # missing this many days instead of on the first run it is missing. Default 0.
    # stale_after_days: int

    def collect(self, conn: psycopg.Connection) -> Iterable[RawPosting]:
        """Yield every currently open posting the source has."""
        ...

    def incomplete_company_ids(self) -> set[int]:
        """Companies whose listings could not be fetched in full on the last run.

        Their postings are not closed, since they may only be missing because of the failure.
        """
        ...
