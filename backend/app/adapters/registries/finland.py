"""Finland: the Finnish Patent and Registration Office's free open-data API for the Business Information System
(YTJ, avoindata.prh.fi, version 3, no key). Used only to confirm a company's legal form, which decides whether the
recipient is a company or an individual for the email rules.

Data minimisation: we read the business ID, the current registered names, the company-form code and the
trade-register and situation fields (bankruptcy, liquidation, restructuring). No person data is requested. An
unknown company form (including private traders, "toiminimi"), an ended company or an ambiguous name returns
"unknown" or None, which the compliance engine blocks.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import httpx

from app.adapters.companies_house.base import RegistryCompany
from app.adapters.http import check, client

BASE_URL = "https://avoindata.prh.fi"
PATH = "/opendata-ytj-api/v3/companies"

# YTJ company-form codes (code list YRMU) -> our normalised legal forms. Anything else is "unknown" (blocked).
FORMS = {
    "5": "partnership",  # AY, avoin yhtiö
    "13": "lp",  # KY, kommandiittiyhtiö
    "16": "ltd",  # OY, osakeyhtiö
    "10": "ltd",  # KOY, keskinäinen kiinteistöosakeyhtiö
    "2": "other_corporate",  # AOY, asunto-osakeyhtiö (housing company)
    "17": "plc",  # OYJ, julkinen osakeyhtiö
    "80": "plc",  # SE, European company
    "14": "other_corporate",  # OK, osuuskunta (co-operative)
    "15": "other_corporate",  # OP, co-operative bank
    "83": "other_corporate",  # SCE
    "84": "other_corporate",  # SCP
    "19": "other_corporate",  # SL, branch of a foreign trader
    "20": "other_corporate",  # SP, savings bank
    "11": "other_corporate",  # KVJ
    "12": "other_corporate",  # KVY
    "23": "other_corporate",  # VOJ
    "24": "other_corporate",  # VOY
    "25": "other_corporate",  # VY
    "9": "other_corporate",  # HY
    "21": "other_corporate",  # TYH
    "6": "other_corporate",  # AYH, aatteellinen yhdistys (non-profit association)
    "18": "charity",  # SÄÄ, säätiö (foundation)
    "22": "public_body",  # VALTLL, state-owned company
}
CURRENT_NAME_TYPES = {"1", "2"}  # 1 = registered name, 2 = parallel name (3 = auxiliary trade name, not used)
_SUFFIX = re.compile(r"\b(oyj|oy|ab|abp|ltd|plc|ky|ay|osk)\b|[^a-z0-9 ]")


def legal_form(code: str | None) -> str:
    return FORMS.get(str(code or "").strip(), "unknown")


def valid_business_id(value: str | None) -> str | None:
    """Y-tunnus: 7 digits, a hyphen and a modulus-11 check digit (weights 7 9 10 5 8 4 2)."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 7:
        digits = "0" + digits  # older IDs are sometimes written without the leading zero
    if len(digits) != 8:
        return None
    rest = sum(int(d) * w for d, w in zip(digits[:7], (7, 9, 10, 5, 8, 4, 2), strict=True)) % 11
    if rest == 1:
        return None
    check_digit = 0 if rest == 0 else 11 - rest
    return f"{digits[:7]}-{digits[7]}" if check_digit == int(digits[7]) else None


def normalise_name(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", _SUFFIX.sub(" ", plain)).strip()


def _current(items: Any) -> list[dict[str, Any]]:
    return [i for i in items or [] if isinstance(i, dict) and not i.get("endDate")]


class FinlandRegistry:
    name = "registry.fi.prh_ytj"

    def __init__(self, *, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None) -> None:
        self._http = client(base_url, transport, headers={"Accept": "application/json"})

    def _search(self, **params: str) -> list[dict[str, Any]]:
        resp = self._http.get(PATH, params=params)
        if resp.status_code == 404:
            return []
        return [c for c in check(resp).json().get("companies") or [] if isinstance(c, dict)]

    @staticmethod
    def _names(c: dict[str, Any]) -> list[str]:
        return [str(n.get("name") or "") for n in _current(c.get("names")) if str(n.get("type")) in CURRENT_NAME_TYPES]

    @classmethod
    def _to_company(cls, c: dict[str, Any]) -> RegistryCompany:
        forms = _current(c.get("companyForms"))
        main = next((str(n.get("name")) for n in _current(c.get("names")) if str(n.get("type")) == "1"), "")
        ended = bool(c.get("endDate"))
        troubled = bool(_current(c.get("companySituations")))  # bankruptcy, liquidation, restructuring
        registered = str(c.get("tradeRegisterStatus")) == "1"
        return RegistryCompany(
            company_number=str((c.get("businessId") or {}).get("value") or ""),
            name=main,
            legal_form=legal_form(forms[0].get("type")) if len(forms) == 1 else "unknown",
            status="dissolved" if ended else "insolvency" if troubled else "active" if registered else "unregistered",
            registered_country="FI",
        )

    def lookup(
        self,
        company_name: str,
        domain: str | None = None,
        company_number: str | None = None,
        country: str | None = None,
    ) -> RegistryCompany | None:
        bid = valid_business_id(company_number)
        if bid:
            hit = [c for c in self._search(businessId=bid) if (c.get("businessId") or {}).get("value") == bid]
            return self._to_company(hit[0]) if len(hit) == 1 else None
        want = normalise_name(company_name)
        if not want:
            return None
        matches = [
            c
            for c in self._search(name=company_name)
            if (c.get("businessId") or {}).get("value") and want in {normalise_name(n) for n in self._names(c)}
        ]
        companies = [self._to_company(c) for c in matches]
        active = [c for c in companies if c.status == "active"] or companies
        return active[0] if len(active) == 1 else None
