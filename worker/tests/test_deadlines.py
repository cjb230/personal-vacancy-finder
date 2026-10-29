"""Reading application deadlines from advert text."""

from datetime import UTC, date, datetime

import pytest

from vacancy_finder.deadlines import find_deadline

POSTED = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize("text, expected", [
    ("Application deadline: 15 November 2026", date(2026, 11, 15)),
    ("Apply by 30/11/2026 via the form.", date(2026, 11, 30)),
    ("Closing date: 2026-11-07", date(2026, 11, 7)),
    ("Applications close on Friday, 6th November.", date(2026, 11, 6)),
    ("The deadline for applications is November 15, 2026.", date(2026, 11, 15)),
    ("Applications are accepted until 01.12.2026", date(2026, 12, 1)),
    ("This position is open until Nov 20th", date(2026, 11, 20)),
    ("Deadline: 11/30/2026", date(2026, 11, 30)),  # US order when the day can't be a month
    ("Please apply before 10 January.", date(2027, 1, 10)),  # no year: the next one
    ("Applications close 1 October", date(2026, 10, 1)),  # just passed, not next year's
])
def test_finds_deadline(text, expected):
    found = find_deadline(f"About us.\n{text}\nMore text.", POSTED)
    assert found.date() == expected
    assert (found.hour, found.minute) == (23, 59) and str(found.tzinfo) == "Europe/Warsaw"


@pytest.mark.parametrize("text", [
    "You will meet tight deadlines. Start date: 1 December 2026.",
    "Founded on 15 November 2019, we apply rigour to everything.",
    "Applications are reviewed on a rolling basis.",
    "Deadline-driven team, since 2012.",
    "Apply by 15 November 2031",  # too far from the posting date to be believable
    "Closing date: 31/02/2026",  # no such day
    None,
])
def test_ignores_other_dates(text):
    assert find_deadline(text, POSTED) is None


def test_date_must_follow_the_phrase_closely():
    text = "Apply by sending us your CV and a short note about a project you are proud of, 15 Nov 2026"
    assert find_deadline(text, POSTED) is None
    assert find_deadline("Apply by:\n15 Nov 2026", POSTED) is None  # not on the same line
