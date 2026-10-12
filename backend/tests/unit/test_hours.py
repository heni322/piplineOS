"""Recipient-local hours, UK nations' different bank holidays, and DST changes (2026 dates)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.deliverability.hours import (
    UnknownJurisdiction,
    is_business_time,
    is_holiday,
    next_business_start,
)


@pytest.mark.parametrize(
    ("day", "sub", "expected"),
    [
        (date(2026, 8, 3), "GB-SCT", True),  # Scotland: summer bank holiday is the FIRST Monday of August
        (date(2026, 8, 3), "GB-ENG", False),
        (date(2026, 8, 31), "GB-ENG", True),  # England & Wales: LAST Monday of August
        (date(2026, 8, 31), "GB-SCT", False),
        (date(2026, 11, 30), "GB-SCT", True),  # St Andrew's Day
        (date(2026, 11, 30), "GB-ENG", False),
        (date(2026, 3, 17), "GB-NIR", True),  # St Patrick's Day (NI)
        (date(2026, 3, 17), "GB-ENG", False),
        (date(2026, 7, 13), "GB-NIR", True),  # Battle of the Boyne (12 July is a Sunday -> substitute Monday)
        (date(2026, 4, 6), "GB-ENG", True),  # Easter Monday: England & Wales and NI...
        (date(2026, 4, 6), "GB-SCT", False),  # ...but not Scotland
        (date(2026, 1, 2), "GB-SCT", True),
        (date(2026, 8, 31), "GB-WLS", True),
    ],
)
def test_uk_nations_have_different_bank_holidays(day: date, sub: str, expected: bool) -> None:
    assert is_holiday(day, "GB", sub) is expected


def test_unknown_uk_nation_avoids_every_nations_holidays() -> None:
    for d in (date(2026, 8, 3), date(2026, 8, 31), date(2026, 11, 30), date(2026, 3, 17)):
        assert is_holiday(d, "GB", None)


def test_ireland_holidays_are_not_uk_holidays() -> None:
    assert is_holiday(date(2026, 3, 17), "IE", None)  # St Patrick's Day
    assert not is_holiday(date(2026, 8, 31), "IE", None)  # UK late summer bank holiday


def test_business_hours_follow_bst_after_dst_change() -> None:
    # Clocks went forward on Sun 29 March 2026. 08:30 UTC on Mon 30 March = 09:30 BST -> inside window.
    assert is_business_time(datetime(2026, 3, 30, 8, 30, tzinfo=UTC), "GB", "GB-ENG")
    # Fri 27 March (still GMT): 08:30 UTC = 08:30 local -> before 09:00.
    assert not is_business_time(datetime(2026, 3, 27, 8, 30, tzinfo=UTC), "GB", "GB-ENG")
    # After clocks go back (Sun 25 Oct 2026), 16:30 UTC = 16:30 GMT -> still inside window.
    assert is_business_time(datetime(2026, 10, 26, 16, 30, tzinfo=UTC), "GB", "GB-ENG")
    assert not is_business_time(datetime(2026, 10, 23, 16, 30, tzinfo=UTC), "GB", "GB-ENG")  # 17:30 BST


def test_next_business_start_skips_weekend_and_holiday() -> None:
    # Friday 28 Aug 2026 18:00 BST -> Mon 31 Aug is an England bank holiday -> Tue 1 Sep 09:00 BST = 08:00 UTC
    got = next_business_start(datetime(2026, 8, 28, 17, 0, tzinfo=UTC), "GB", "GB-ENG")
    assert got == datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def test_next_business_start_returns_now_inside_window() -> None:
    t = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
    assert next_business_start(t, "GB", "GB-ENG") == t


def test_recipient_timezone_is_used() -> None:
    t = datetime(2026, 10, 13, 7, 30, tzinfo=UTC)  # 09:30 in Paris (CEST), 08:30 in London (BST)
    assert is_business_time(t, "FR")
    assert not is_business_time(t, "GB", "GB-ENG")


def test_unknown_country_raises() -> None:
    with pytest.raises(UnknownJurisdiction):
        is_business_time(datetime(2026, 10, 13, 10, tzinfo=UTC), "XX")
