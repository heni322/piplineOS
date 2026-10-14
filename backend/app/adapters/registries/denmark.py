"""Denmark: the Danish Business Authority's (Erhvervsstyrelsen) CVR system-to-system access, free of charge with a
user ID and password it issues on request. Used only to confirm a company's legal form and status, which decides
whether the recipient is a company or an individual for the email rules.

Data minimisation: the search asks only for the CVR number, the current name, the current legal-form code and the
composite status (`_source` filter). Participants (owners, board members) are never requested. Sole
proprietorships ("enkeltmandsvirksomhed") count as individuals. An unknown code, an ambiguous name or a missing
entry returns "unknown" or None, which blocks.

Field names follow Erhvervsstyrelsen's published Elasticsearch mapping; the sandbox check at go-live confirms them
against the real service (docs/GO-LIVE-RUNBOOK.md).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import httpx

from app.adapters.companies_house.base import RegistryCompany
from app.adapters.http import check, client

BASE_URL = "https://distribution.virk.dk"  # credentials are only ever sent over TLS (see __init__)
SEARCH = "/cvr-permanent/virksomhed/_search"
META = "Vrvirksomhed.virksomhedMetadata"
FIELDS = [
    "Vrvirksomhed.cvrNummer",
    f"{META}.nyesteNavn.navn",
    f"{META}.nyesteVirksomhedsform.virksomhedsformkode",
    f"{META}.sammensatStatus",
]

# CVR "virksomhedsform" codes -> our normalised legal forms. Anything else is "unknown" (blocked).
FORMS = {
    "10": "sole_trader",  # enkeltmandsvirksomhed
    "30": "partnership",  # interessentskab (I/S)
    "40": "lp",  # kommanditselskab (K/S)
    "60": "plc",  # aktieselskab (A/S)
    "70": "lp",  # partnerselskab / kommanditaktieselskab (P/S): cautious
    "80": "ltd",  # anpartsselskab (ApS)
    "81": "ltd",  # iværksætterselskab (IVS)
}
ACTIVE = {"normal", "aktiv"}
INSOLVENT = ("under konkurs", "under tvangsopløsning", "under frivillig likvidation", "under rekonstruktion")
_SUFFIX = re.compile(r"\b(aps|a s|ivs|i s|k s|p s|as)\b|[^a-z0-9 ]")


def legal_form(code: Any) -> str:
    return FORMS.get(str(code or "").strip(), "unknown")


def valid_cvr(value: str | None) -> str | None:
    """Eight digits with the modulus-11 check (weights 2 7 6 5 4 3 2 1)."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != 8 or digits[0] == "0":
        return None
    return digits if sum(int(d) * w for d, w in zip(digits, (2, 7, 6, 5, 4, 3, 2, 1), strict=True)) % 11 == 0 else None


def normalise_name(name: str) -> str:
    plain = name.lower().replace("ø", "o").replace("æ", "ae").replace("/", " ")
    plain = unicodedata.normalize("NFKD", plain).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", _SUFFIX.sub(" ", plain)).strip()


def _status(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in ACTIVE:
        return "active"
    if s.startswith(INSOLVENT):
        return "insolvency"
    return "dissolved" if s else "unknown"


class DenmarkRegistry:
    name = "registry.dk.cvr"

    def __init__(
        self, username: str, password: str, *, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not username or not password:
            raise ValueError("CVR credentials missing")
        if not base_url.startswith("https://"):
            raise ValueError("CVR credentials are only sent over HTTPS")
        self._http = client(base_url, transport, auth=(username, password), headers={"Accept": "application/json"})

    def _search(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        body = {"_source": FIELDS, "size": 10, "query": query}
        hits = _get(check(self._http.post(SEARCH, json=body)).json(), "hits", "hits") or []
        return [v for h in hits if isinstance(v := _get(h, "_source", "Vrvirksomhed"), dict)]

    @staticmethod
    def _to_company(v: dict[str, Any]) -> RegistryCompany:
        meta = v.get("virksomhedMetadata") or {}
        return RegistryCompany(
            company_number=str(v.get("cvrNummer") or ""),
            name=str(_get(meta, "nyesteNavn", "navn") or ""),
            legal_form=legal_form(_get(meta, "nyesteVirksomhedsform", "virksomhedsformkode")),
            status=_status(meta.get("sammensatStatus")),
            registered_country="DK",
        )

    def lookup(
        self,
        company_name: str,
        domain: str | None = None,
        company_number: str | None = None,
        country: str | None = None,
    ) -> RegistryCompany | None:
        cvr = valid_cvr(company_number)
        if cvr:
            found = [
                v for v in self._search({"term": {"Vrvirksomhed.cvrNummer": cvr}}) if str(v.get("cvrNummer")) == cvr
            ]
            return self._to_company(found[0]) if len(found) == 1 else None
        want = normalise_name(company_name)
        if not want:
            return None
        found = self._search({"match_phrase": {f"{META}.nyesteNavn.navn": company_name}})
        companies = [
            self._to_company(v)
            for v in found
            if v.get("cvrNummer") and normalise_name(self._to_company(v).name) == want
        ]
        active = [c for c in companies if c.status == "active"] or companies
        return active[0] if len(active) == 1 else None


def _get(d: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d
