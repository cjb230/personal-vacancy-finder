from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass
class RawPosting:
    """One advert as a collector found it, before it is stored."""

    external_id: str
    url: str
    title: str
    company_name: str
    location_text: str | None = None
    work_mode: str = "unknown"  # remote | hybrid | onsite | unknown
    description: str | None = None
    language: str | None = None
    contract_type: str | None = None
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str | None = None
    salary_period: str | None = None  # hour | day | month | year
    posted_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    # Set by ATS collectors so the company row records where its feed lives.
    ats_kind: str | None = None
    ats_slug: str | None = None
    # True when the title is a placeholder, so the posting mustn't merge with others by title.
    unique_job: bool = False
