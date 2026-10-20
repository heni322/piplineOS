"""French public contract awards from the BOAMP (Bulletin officiel des annonces des marchés publics), France's official
procurement bulletin, published by the DILA as open data.

The API (Opendatasoft "explore" v2.1) is free and needs no key. The data is published under the Licence Ouverte /
Open Licence (Etalab): reuse, commercial too, is allowed with attribution ("Source: BOAMP, DILA"). Checked live on
2026-10-19: award notices ("ATTRIBUTION") carry the winners' names in `titulaire` (a list), the contract title in
French in `objet`, the buyer in `nomacheteur`, the date in `dateparution` and a link in `url_avis`.

For clients targeting France this replaces TED: it covers the same EU-level notices (family JOUE) plus national
simplified-procedure ones (MAPA), with the titles in French, so French keywords ("infogérance", "sécurité
informatique") match. National notices that do not fill `titulaire` (family FNS) are not used: their winners are only
inside the full notice document.

What we take, and nothing more: the winning organisations' names, the title, buyer, date and notice number. Contact
points in the notice are never requested.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Any

import httpx

from app.adapters.http import check, client
from app.adapters.signals.base import RawSignal
from app.adapters.signals.ocds import _matches, parse_query

BOAMP_API = "https://boamp-datadila.opendatasoft.com"
RECORDS = "/api/explore/v2.1/catalog/datasets/boamp/records"
NOTICE_PAGE = "https://www.boamp.fr/pages/avis/?q=idweb:{n}"
FIELDS = "idweb,famille,objet,nomacheteur,dateparution,titulaire,url_avis"


def fold(text: str) -> str:
    """Lower case without accents, so "Sécurité" matches "securite" (and the other way round)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def _names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return [str(value)]


class BoampAwardSource:
    name = "boamp.live"

    def __init__(
        self, *, transport: httpx.BaseTransport | None = None, page_size: int = 100, max_pages: int = 10
    ) -> None:
        self._http = client(BOAMP_API, transport, headers={"Accept": "application/json"})
        self._page_size, self._max_pages = min(page_size, 100), max_pages  # the API returns at most 100 a page

    @staticmethod
    def where(since: datetime) -> str:
        return f"nature=\"ATTRIBUTION\" and dateparution>=date'{since.date().isoformat()}' and titulaire is not null"

    def fetch(self, query: str, since: datetime) -> list[RawSignal]:
        include, exclude = parse_query(query)
        if not include:
            return []
        include, exclude = [fold(p) for p in include], [fold(p) for p in exclude]
        out: list[RawSignal] = []
        for page in range(self._max_pages):
            params: dict[str, str | int] = {
                "select": FIELDS,
                "where": self.where(since),
                "order_by": "dateparution desc",
                "limit": self._page_size,
                "offset": page * self._page_size,
            }
            records = check(self._http.get(RECORDS, params=params)).json().get("results") or []
            for r in records:
                out.extend(self._signals(r, include, exclude))
            if len(records) < self._page_size:
                break
        return out

    def _signals(self, r: dict[str, Any], include: list[str], exclude: list[str]) -> list[RawSignal]:
        number = str(r.get("idweb") or "").strip()
        title = " ".join(str(r.get("objet") or "").split())
        folded = fold(title)
        if not number or not title or not _matches(folded, include) or (exclude and _matches(folded, exclude)):
            return []
        winners = list(dict.fromkeys(w.strip() for w in _names(r.get("titulaire")) if w.strip()))
        buyer = " ".join(str(r.get("nomacheteur") or "").split()) or "a public body"
        try:
            when = datetime.fromisoformat(str(r.get("dateparution"))[:10] + "T00:00:00+00:00")
        except ValueError:
            when = datetime.now().astimezone()
        url = str(r.get("url_avis") or "")
        if not url.startswith("https://www.boamp.fr/"):  # only BOAMP's own notice pages are linked
            url = NOTICE_PAGE.format(n=number)
        return [
            RawSignal(
                company_name=w[:200],
                domain=None,
                # published in France's bulletin; the French register (Sirene) then confirms the company
                country_hint="France",
                signal_type="tender",
                title=f"Awarded: {title}"[:200],
                text=f"{buyer} awarded the contract '{title}' to {w} (BOAMP notice {number}).",
                source_url=url,
                detected_at=when,
                source_record_id=f"boamp:{number}:{i}",
            )
            for i, w in enumerate(winners)
        ]
