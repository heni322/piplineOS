"""EU contract awards from TED (Tenders Electronic Daily), the EU's official procurement journal.

TED's search API (v3) is free, needs no key, and its notices may be reused, commercially too, with attribution
("Source: TED, Publications Office of the EU"). Fair use: at most 700 requests a minute per IP, so we read a few
pages per run. Same signal as the UK award notices: a supplier winning a public contract.

What we take, and nothing more: the winning organisation's name and country, the notice title, buyer, date and
publication number. Contact points in the notice are never requested.

Only notices above the EU thresholds are on TED; smaller national contracts are not covered.

The API's field names are listed in FIELDS. They were taken from TED's published search fields and must be checked
against the live service once (`pipelineos signals check ted`) before relying on this source.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from app.adapters.http import check, client
from app.adapters.signals.base import RawSignal
from app.adapters.signals.ocds import _matches, parse_query

TED_API = "https://api.ted.europa.eu"
NOTICE_PAGE = "https://ted.europa.eu/en/notice/-/detail/{n}"
FIELDS = [
    "publication-number",
    "publication-date",
    "notice-title",
    "buyer-name",
    "winner-name",
    "organisation-country-tenderer",
]
# ISO alpha-3 (TED) -> the country names our location parser understands
COUNTRY = {
    "IRL": "Ireland",
    "FRA": "France",
    "BEL": "Belgium",
    "NLD": "Netherlands",
    "ESP": "Spain",
    "ITA": "Italy",
    "PRT": "Portugal",
    "NOR": "Norway",
    "SWE": "Sweden",
    "DNK": "Denmark",
    "FIN": "Finland",
    "DEU": "Germany",
    "AUT": "Austria",
    "LUX": "Luxembourg",
}
ALPHA3 = {"IE": "IRL", "FR": "FRA", "BE": "BEL", "NL": "NLD", "ES": "ESP", "IT": "ITA", "PT": "PRT", "NO": "NOR",
          "SE": "SWE", "DK": "DNK", "FI": "FIN", "DE": "DEU", "AT": "AUT", "LU": "LUX"}  # fmt: skip


def _texts(value: Any) -> list[str]:
    """TED values come as plain strings, lists, or per-language maps ({"eng": ..., "fra": ...}): prefer English."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    if isinstance(value, dict):
        pick = value.get("eng") or value.get("ENG") or next(iter(value.values()), None)
        return _texts(pick)
    return [str(value)]


def _date(value: Any) -> datetime | None:
    for t in _texts(value):
        try:
            return datetime.fromisoformat(t[:10] + "T00:00:00+00:00")
        except ValueError:
            continue
    return None


class TedAwardSource:
    name = "ted.live"

    def __init__(
        self,
        countries: list[str],
        *,
        transport: httpx.BaseTransport | None = None,
        page_size: int = 100,
        max_pages: int = 5,
    ) -> None:
        self._countries = [ALPHA3[c] for c in countries if c in ALPHA3]  # GB is not on TED since 2021
        self._http = client(TED_API, transport, headers={"Accept": "application/json"})
        self._page_size, self._max_pages = page_size, max_pages

    def query(self, since: datetime) -> str:
        return (
            f"notice-type IN (can-standard can-social) AND publication-date>={since.strftime('%Y%m%d')} "
            f"AND organisation-country-tenderer IN ({' '.join(self._countries)})"
        )

    def fetch(self, query: str, since: datetime) -> list[RawSignal]:
        include, exclude = parse_query(query)
        if not self._countries or not include:
            return []
        out: list[RawSignal] = []
        for page in range(1, self._max_pages + 1):
            body = {
                "query": self.query(since),
                "fields": FIELDS,
                "page": page,
                "limit": self._page_size,
                "paginationMode": "PAGE_NUMBER",
            }
            notices = check(self._http.post("/v3/notices/search", json=body)).json().get("notices") or []
            for n in notices:
                out.extend(self._signals(n, include, exclude))
            if len(notices) < self._page_size:
                break
        return out

    def _signals(self, n: dict[str, Any], include: list[str], exclude: list[str]) -> list[RawSignal]:
        number = next(iter(_texts(n.get("publication-number"))), "")
        title = next(iter(_texts(n.get("notice-title"))), "").strip()
        if not number or not title or not _matches(title, include) or (exclude and _matches(title, exclude)):
            return []
        winners = list(dict.fromkeys(w.strip() for w in _texts(n.get("winner-name")) if w.strip()))
        countries = _texts(n.get("organisation-country-tenderer"))  # one per winner, in order, when given
        buyer = next(iter(_texts(n.get("buyer-name"))), "a public body")
        when = _date(n.get("publication-date")) or datetime.now().astimezone()
        out = []
        for i, w in enumerate(winners):
            code = countries[i] if i < len(countries) else (countries[0] if len(countries) == 1 else "")
            country = COUNTRY.get(code)  # unknown -> no hint: the compliance engine then blocks (country unknown)
            out.append(
                RawSignal(
                    company_name=w[:200],
                    domain=None,
                    country_hint=country,
                    signal_type="tender",
                    title=f"Awarded: {title}"[:200],
                    text=f"{buyer} awarded the contract '{title}' to {w} (TED notice {number}).",
                    source_url=NOTICE_PAGE.format(n=number),
                    detected_at=when,
                    source_record_id=f"ted:{number}:{i}",
                )
            )
        return out
