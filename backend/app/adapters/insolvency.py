"""Insolvency filter: never pitch a company that is being wound up.

Two free, official sources:
- **The Gazette** (thegazette.co.uk, Open Government Licence): corporate insolvency notices, the earliest public
  warning (a winding-up petition is advertised there before the register's status changes). Searched by company
  number within the corporate-insolvency category; only company notices are read, never personal insolvency.
- **The Companies House register**: a status other than active ("liquidation", "administration",
  "receivership", "insolvency-proceedings", "dissolved"...).

A source that fails is skipped (logged): the register status is checked again by Agent 2 before any contact.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # element types and ParseError only; parsing goes through defusedxml
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring

from app.adapters.base import RateLimited, RetryableProviderError
from app.adapters.http import ProviderError, check, client
from app.adapters.signals.ocds import _parse_dt, normalise_company_number
from app.core.logging import get_logger

log = get_logger(__name__)
GAZETTE = "https://www.thegazette.co.uk"
CORPORATE_INSOLVENCY = "G205000000"  # the Gazette's "Corporate insolvency" notice category
ATOM = "{http://www.w3.org/2005/Atom}"
OK_STATUSES = {"active", "open"}


class InsolvencyCheck(Protocol):
    def is_insolvent(self, company_number: str) -> str | None:
        """A short reason when the company is in an insolvency process or no longer active, else None."""
        ...


class Profiles(Protocol):
    def profile(self, company_number: str) -> dict[str, Any] | None: ...


class GazetteNotices:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, lookback_days: int = 365) -> None:
        self._http = client(GAZETTE, transport, headers={"Accept": "application/atom+xml"})
        self._lookback = timedelta(days=lookback_days)

    def recent(self, company_number: str, now: datetime | None = None) -> list[str]:
        """Titles of corporate-insolvency notices that state this company number, newest first, within lookback."""
        resp = self._http.get(
            "/all-notices/notice/data.feed",
            params={"text": f'"{company_number}"', "categorycode": CORPORATE_INSOLVENCY, "results-page-size": 20},
        )
        root = safe_fromstring(check(resp).content)  # no entities, no external references
        cutoff = (now or datetime.now(UTC)) - self._lookback
        out = []
        for e in root.iter(f"{ATOM}entry"):
            when = _parse_dt((e.findtext(f"{ATOM}updated") or e.findtext(f"{ATOM}published") or "").strip())
            body = " ".join(t for t in e.itertext() if t)
            # the full-text search can match a number inside another one: require the exact number as a word
            if when is None or when < cutoff or not _mentions(body, company_number):
                continue
            out.append((e.findtext(f"{ATOM}title") or "insolvency notice").strip()[:200])
        return out


def _mentions(body: str, number: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(number)}(?![A-Za-z0-9])", body) is not None


class InsolvencyFilter:
    """Gazette notices first (earliest warning), then the register status. Results are cached for one run."""

    def __init__(self, gazette: GazetteNotices | None, register: Profiles | None) -> None:
        self._gazette, self._register = gazette, register
        self._cache: dict[str, str | None] = {}
        self.failures = 0

    def is_insolvent(self, company_number: str) -> str | None:
        number = normalise_company_number(company_number)
        if number is None:
            return None
        if number not in self._cache:
            self._cache[number] = self._check(number)
        return self._cache[number]

    def _check(self, number: str) -> str | None:
        if self._gazette is not None:
            try:
                notices = self._gazette.recent(number)
            except (
                ProviderError,
                RateLimited,
                RetryableProviderError,
                httpx.HTTPError,
                ET.ParseError,
                DefusedXmlException,
            ) as exc:
                self.failures += 1
                log.warning("gazette_check_failed", error=type(exc).__name__)
            else:
                if notices:
                    return f"gazette:{notices[0]}"
        if self._register is not None:
            try:
                prof = self._register.profile(number)
            except (ProviderError, RateLimited, RetryableProviderError, httpx.HTTPError) as exc:
                self.failures += 1
                log.warning("register_status_check_failed", error=type(exc).__name__)
                return None
            if prof is not None and str(prof.get("company_status") or "") not in OK_STATUSES:
                return f"register_status:{prof.get('company_status') or 'unknown'}"
        return None
