from collections.abc import Iterable
from typing import Protocol

from ..models import RawPosting


class Collector(Protocol):
    """A source of postings. One collector covers one source key."""

    source_key: str  # matches sources.key
    source_name: str
    source_kind: str  # ats | board | aggregator | email | custom

    def collect(self) -> Iterable[RawPosting]:
        """Yield every currently open posting the source has."""
        ...
