"""Recipient-local business hours, with bank holidays per UK nation (and per country).

If a UK prospect's nation is unknown we avoid the UNION of all UK nations' bank holidays (safest).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays

DEFAULT_TZ = {
    "GB": "Europe/London",
    "IE": "Europe/Dublin",
    "DE": "Europe/Berlin",
    "AT": "Europe/Vienna",
    "FR": "Europe/Paris",
    "NL": "Europe/Amsterdam",
    "BE": "Europe/Brussels",
    "ES": "Europe/Madrid",
    "PT": "Europe/Lisbon",
    "IT": "Europe/Rome",
    "SE": "Europe/Stockholm",
    "DK": "Europe/Copenhagen",
    "FI": "Europe/Helsinki",
    "NO": "Europe/Oslo",
}
UK_SUBDIVS = {"GB-ENG": "ENG", "GB-WLS": "WLS", "GB-SCT": "SCT", "GB-NIR": "NIR"}
WINDOW_START, WINDOW_END = time(9, 0), time(17, 0)


class UnknownJurisdiction(Exception):
    pass


@lru_cache(maxsize=256)
def _holiday_dates(country: str, subdivision: str | None, year: int) -> frozenset[date]:
    if country == "GB":
        subs = [UK_SUBDIVS[subdivision]] if subdivision in UK_SUBDIVS else list(UK_SUBDIVS.values())
        days: set[date] = set()
        for s in subs:
            days |= set(holidays.country_holidays("GB", subdiv=s, years=year).keys())
        return frozenset(days)
    try:
        return frozenset(holidays.country_holidays(country, years=year).keys())
    except NotImplementedError as exc:
        raise UnknownJurisdiction(country) from exc


def is_holiday(day: date, country: str, subdivision: str | None) -> bool:
    return day in _holiday_dates(country, subdivision, day.year)


def tz_for(country: str) -> ZoneInfo:
    if country not in DEFAULT_TZ:
        raise UnknownJurisdiction(country)
    return ZoneInfo(DEFAULT_TZ[country])


def is_business_time(at: datetime, country: str, subdivision: str | None = None) -> bool:
    local = at.astimezone(tz_for(country))
    return (
        local.weekday() < 5
        and not is_holiday(local.date(), country, subdivision)
        and WINDOW_START <= local.time() < WINDOW_END
    )


def next_business_start(at: datetime, country: str, subdivision: str | None = None) -> datetime:
    """Earliest instant >= ``at`` inside the recipient's business window (DST-correct via zoneinfo)."""
    if is_business_time(at, country, subdivision):
        return at
    zone = tz_for(country)
    local = at.astimezone(zone)
    day = local.date() if local.time() < WINDOW_START else local.date() + timedelta(days=1)
    for _ in range(30):
        if day.weekday() < 5 and not is_holiday(day, country, subdivision):
            return datetime.combine(day, WINDOW_START, tzinfo=zone).astimezone(UTC)
        day += timedelta(days=1)
    raise RuntimeError("no business day found within 30 days")
