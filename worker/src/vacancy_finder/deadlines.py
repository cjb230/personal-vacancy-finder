"""Application deadlines written in advert text, e.g. "Apply by 15 November 2026" or
"Closing date: 30/11/2026".

Only English wording is recognised (Polish adverts are left out of matching anyway). A date
needs one of the deadline phrases just before it, so "start date: 1 December" or "meet tight
deadlines" are not read as deadlines. A date with no time means the end of that day in
DISPLAY_TZ.
"""

import os
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DISPLAY_TZ = os.environ.get("DISPLAY_TZ", "Europe/Warsaw")

_MONTHS = {name: i for i, names in enumerate((
    ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
    ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
    ("oct", "october"), ("nov", "november"), ("dec", "december"),
), start=1) for name in names}
_MONTH = r"(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(\d{4}))?"

_PHRASE = re.compile(
    r"\b(?:application\s+deadline|deadline\s+(?:for|to)\s+(?:applications?|apply(?:ing)?)"
    r"|closing\s+date|applications?\s+(?:close|closes|closing|due|deadline)"
    r"|applications?\s+(?:must|should)\s+be\s+(?:received|submitted|sent)\s+(?:by|before|no\s+later\s+than)"
    r"|apply\s+(?:by|before|until|till|no\s+later\s+than)"
    r"|applications?\s+(?:are\s+|will\s+be\s+)?(?:accepted|open|welcome|considered)\s+(?:until|till|through|to)"
    r"|(?:position|role|vacancy|posting|advert|job|recruitment)\s+(?:is\s+|will\s+be\s+|remains\s+)?open\s+(?:until|till|through)"
    r"|deadline)\b",
    re.I,
)
# How far after the phrase the date may start, for "is", "on", a colon, a weekday...
_GAP = 40
_DATES = [
    ("ymd", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    ("numeric", re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})\b")),
    ("dmy", re.compile(r"\b" + _DAY + r"(?:\s+of)?\s+" + _MONTH + _YEAR + r"\b", re.I)),
    ("mdy", re.compile(r"\b" + _MONTH + r"\s+" + _DAY + _YEAR + r"\b", re.I)),
]
# A deadline further than this from the advert's date is more likely a misread.
_MAX_SPAN = timedelta(days=366)


def _to_date(kind: str, groups: tuple, reference: date) -> date | None:
    year = None
    if kind == "ymd":
        year, month, day = (int(g) for g in groups)
    elif kind == "numeric":
        day, month, year = (int(g) for g in groups)
        if month > 12 >= day:  # 11/30/2026, written the US way
            day, month = month, day
        if year < 100:
            year += 2000
    elif kind == "dmy":
        day, month, year = int(groups[0]), _MONTHS[groups[1].lower()], groups[2] and int(groups[2])
    else:
        month, day, year = _MONTHS[groups[0].lower()], int(groups[1]), groups[2] and int(groups[2])
    try:
        if year:
            return date(year, month, day)
        # No year: the next such date, allowing for an advert seen a little after its deadline.
        found = date(reference.year, month, day)
        return found if found >= reference - timedelta(days=60) else date(reference.year + 1, month, day)
    except ValueError:
        return None


def find_deadline(text: str | None, reference: datetime | None = None) -> datetime | None:
    """The first application deadline stated in `text`, as the end of that day in DISPLAY_TZ.
    `reference` is when the advert was posted (default now), used to fill in a missing year
    and to drop dates too far from it."""
    if not text:
        return None
    zone = ZoneInfo(DISPLAY_TZ)
    reference = reference or datetime.now(zone)
    ref_day = (reference.astimezone(zone) if reference.tzinfo else reference).date()
    for phrase in _PHRASE.finditer(text):
        window = text[phrase.end(): phrase.end() + _GAP + 30].split("\n", 1)[0]
        matches = [(m.start(), kind, m) for kind, pattern in _DATES
                   if (m := pattern.search(window)) and m.start() <= _GAP]
        if not matches:
            continue
        _, kind, match = min(matches, key=lambda found: found[0])
        found = _to_date(kind, match.groups(), ref_day)
        if found and abs(found - ref_day) <= _MAX_SPAN:
            return datetime.combine(found, time(23, 59, 59), zone)
    return None
