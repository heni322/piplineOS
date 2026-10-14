"""Norway: the Brønnøysund Register Centre's free open API for the Central Coordinating Register for Legal
Entities (Enhetsregisteret, data.brreg.no, no key). Used only to confirm a company's legal form, which decides
whether the recipient is a company or an individual (sole proprietor) for the email rules.

Data minimisation: we read the organisation number, the name, the organisation-form code and the bankruptcy /
winding-up flags. The roles endpoint (board members, managing director) is never called. A deleted entity, an
unknown code or an ambiguous name returns "unknown" or None, which the compliance engine blocks.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import httpx

from app.adapters.companies_house.base import RegistryCompany
from app.adapters.http import check, client

BASE_URL = "https://data.brreg.no"

# Enhetsregisteret "organisasjonsform" codes -> our normalised legal forms (app.domain.compliance.subscriber).
# Anything not listed is "unknown" (blocked). Where the categories differ, the mapping takes the cautious side.
FORMS = {
    "ENK": "sole_trader",  # enkeltpersonforetak
    "ANS": "partnership",  # ansvarlig selskap
    "DA": "partnership",  # selskap med delt ansvar
    "KS": "lp",  # kommandittselskap
    "PRE": "partnership",  # partsrederi
    "SAM": "partnership",  # tingsrettslig sameie
    "AS": "ltd",  # aksjeselskap
    "ASA": "plc",  # allmennaksjeselskap
    "SE": "plc",  # europeisk selskap
    "NUF": "other_corporate",  # norskregistrert utenlandsk foretak (foreign company's branch)
    "SA": "other_corporate",  # samvirkeforetak (cooperative)
    "BA": "other_corporate",  # selskap med begrenset ansvar
    "BRL": "other_corporate",  # borettslag
    "FLI": "other_corporate",  # forening/lag/innretning
    "STI": "charity",  # stiftelse
    "STAT": "public_body",
    "FYLK": "public_body",
    "KOMM": "public_body",
    "SF": "public_body",  # statsforetak
    "KF": "public_body",  # kommunalt foretak
    "FKF": "public_body",  # fylkeskommunalt foretak
    "IKS": "public_body",  # interkommunalt selskap
    "ORGL": "public_body",  # organisasjonsledd
}
_SUFFIX = re.compile(r"\b(asa|as|ans|da|ks|sa|nuf)\b|[^a-z0-9 ]")


def legal_form(code: str | None) -> str:
    return FORMS.get((code or "").strip().upper(), "unknown")


def valid_orgnr(value: str | None) -> str | None:
    """Nine digits with the modulus-11 check digit (weights 3 2 7 6 5 4 3 2)."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != 9:
        return None
    rest = sum(int(d) * w for d, w in zip(digits[:8], (3, 2, 7, 6, 5, 4, 3, 2), strict=True)) % 11
    check_digit = 0 if rest == 0 else 11 - rest
    return digits if check_digit != 10 and check_digit == int(digits[8]) else None


def normalise_name(name: str) -> str:
    plain = name.lower().replace("ø", "o").replace("æ", "ae")
    plain = unicodedata.normalize("NFKD", plain).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", _SUFFIX.sub(" ", plain)).strip()


class NorwayRegistry:
    name = "registry.no.brreg"

    def __init__(self, *, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None) -> None:
        self._http = client(base_url, transport, headers={"Accept": "application/json"})

    @staticmethod
    def _to_company(e: dict[str, Any]) -> RegistryCompany:
        winding_up = any(e.get(k) for k in ("konkurs", "underAvvikling", "underTvangsavviklingEllerTvangsopplosning"))
        deleted = bool(e.get("slettedato"))
        return RegistryCompany(
            company_number=str(e["organisasjonsnummer"]),
            name=str(e.get("navn") or ""),
            legal_form=legal_form((e.get("organisasjonsform") or {}).get("kode")),
            status="dissolved" if deleted else "insolvency" if winding_up else "active",
            registered_country="NO",
        )

    def lookup(
        self,
        company_name: str,
        domain: str | None = None,
        company_number: str | None = None,
        country: str | None = None,
    ) -> RegistryCompany | None:
        orgnr = valid_orgnr(company_number)
        if orgnr:
            resp = self._http.get(f"/enhetsregisteret/api/enheter/{orgnr}")
            if resp.status_code in (404, 410):  # unknown, or deleted from the register
                return None
            e = check(resp).json()
            return self._to_company(e) if str(e.get("organisasjonsnummer")) == orgnr else None
        want = normalise_name(company_name)
        if not want:
            return None
        data = check(self._http.get("/enhetsregisteret/api/enheter", params={"navn": company_name, "size": 10})).json()
        found = [e for e in (data.get("_embedded") or {}).get("enheter") or [] if isinstance(e, dict)]
        matches = [
            e for e in found if e.get("organisasjonsnummer") and normalise_name(str(e.get("navn") or "")) == want
        ]
        companies = [self._to_company(e) for e in matches]
        active = [c for c in companies if c.status == "active"] or companies
        return active[0] if len(active) == 1 else None
