"""The countries the platform serves (SERVED_COUNTRIES, France by default): the only countries a client can be
established in, target, search or enable. Checked where a country is chosen or saved (API, console, CLI), so the UK
can no longer be picked in production. The compliance engine stays the last barrier: a country that is not enabled
is never emailed.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.settings import Settings

NAMES = {"FR": "France", "GB": "United Kingdom"}
CURRENCY = {"FR": "EUR", "GB": "GBP"}
TIMEZONE = {"FR": "Europe/Paris", "GB": "Europe/London"}


class NotServed(ValueError):
    def __init__(self, codes: list[str], served: tuple[str, ...]) -> None:
        self.codes, self.served = codes, served
        super().__init__(
            f"Not a country this platform serves: {', '.join(codes)}. Served: {', '.join(served)} (SERVED_COUNTRIES)."
        )


def not_served(settings: Settings, codes: Iterable[str | None]) -> list[str]:
    served = set(settings.served)
    return sorted({c.strip().upper() for c in codes if c and c.strip() and c.strip().upper() not in served})


def require_served(settings: Settings, codes: Iterable[str | None]) -> None:
    if bad := not_served(settings, codes):
        raise NotServed(bad, settings.served)
