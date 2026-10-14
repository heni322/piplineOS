"""Sweden: Bolagsverket's free API for high-value datasets ("Värdefulla datamängder", EU Regulation 2023/138),
OAuth client credentials issued free of charge after registration. Used only to confirm a company's legal form and
status, which decides whether the recipient is a company or an individual for the email rules.

Lookups are by organisation number only (the API has no name search), so a company without a known number is
"unknown", which blocks. Data minimisation:
- A Swedish sole trader is identified by a personal identity number ("personnummer"). We never send one: a number
  whose third and fourth digits are below 20 is a personal number, and the lookup returns "unknown" without a call.
- We read the organisation number, the current name, the legal-form code (Statistics Sweden "juridisk form") and
  the deregistration and liquidation fields. Board members and representatives are never requested.

Field names follow Bolagsverket's published schema; the sandbox check at go-live confirms them against the real
service (docs/GO-LIVE-RUNBOOK.md). Anything missing or unexpected returns "unknown".
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from app.adapters.companies_house.base import RegistryCompany
from app.adapters.http import check, client

BASE_URL = "https://gw.api.bolagsverket.se"
API_PATH = "/vardefulla-datamangder/v1"
TOKEN_PATH = "/oauth2/token"
SCOPE = "vardefulla-datamangder:read"

# Statistics Sweden "juridisk form" codes -> our normalised legal forms. Anything else is "unknown" (blocked).
FORMS = {
    "10": "sole_trader",  # fysiska personer (enskild näringsidkare)
    "21": "partnership",  # enkla bolag
    "22": "partnership",  # partrederier
    "31": "partnership",  # handelsbolag, kommanditbolag (cautious: treated like UK partnerships)
    "41": "ltd",  # bankaktiebolag
    "42": "ltd",  # försäkringsaktiebolag
    "43": "plc",  # europabolag
    "49": "ltd",  # övriga aktiebolag
    "51": "other_corporate",  # ekonomiska föreningar
    "53": "other_corporate",  # bostadsrättsföreningar
    "54": "other_corporate",  # kooperativa hyresrättsföreningar
    "61": "other_corporate",  # ideella föreningar
    "62": "other_corporate",  # samfälligheter
    "63": "other_corporate",  # registrerade trossamfund
    "71": "charity",  # familjestiftelser
    "72": "charity",  # övriga stiftelser och fonder
    "81": "public_body",  # statliga enheter
    "82": "public_body",  # kommuner
    "83": "public_body",  # kommunalförbund
    "84": "public_body",  # regioner
    "92": "other_corporate",  # ömsesidiga försäkringsbolag
    "93": "other_corporate",  # sparbanker
    "96": "other_corporate",  # utländska juridiska personer (Swedish branch)
}


def legal_form(code: str | None) -> str:
    return FORMS.get(str(code or "").strip(), "unknown")


def valid_orgnr(value: str | None) -> str | None:
    """Ten digits with a Luhn check digit, of a legal person (third and fourth digits >= 20). Personal identity
    numbers (sole traders) return None so that they are never sent anywhere."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("16"):  # some systems prefix legal persons with "16"
        digits = digits[2:]
    if len(digits) != 10 or int(digits[2:4]) < 20:
        return None
    total = 0
    for i, d in enumerate(digits[:9]):
        n = int(d) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    return digits if (10 - total % 10) % 10 == int(digits[9]) else None


def _get(d: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


class SwedenRegistry:
    name = "registry.se.bolagsverket"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        base_url: str = BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("Bolagsverket client credentials missing")
        self._http = client(base_url, transport, headers={"Accept": "application/json"})
        self._auth = (client_id, client_secret)
        self._token: str | None = None
        self._expires = 0.0

    def _bearer(self) -> str:
        if self._token is None or time.monotonic() >= self._expires:
            data = check(
                self._http.post(TOKEN_PATH, data={"grant_type": "client_credentials", "scope": SCOPE}, auth=self._auth)
            ).json()
            self._token = str(data["access_token"])
            self._expires = time.monotonic() + max(int(data.get("expires_in", 300)) - 30, 30)
        return self._token

    @staticmethod
    def _to_company(o: dict[str, Any], orgnr: str) -> RegistryCompany:
        names = _get(o, "organisationsnamn", "organisationsnamnLista") or []
        name = next((str(n.get("namn")) for n in names if isinstance(n, dict) and n.get("namn")), "")
        deregistered = bool(_get(o, "avregistreradOrganisation", "avregistreringsdatum"))
        winding_up = bool(
            _get(o, "pagaendeAvvecklingEllerOmstrukturering", "pagaendeAvvecklingEllerOmstruktureringsforfarandeLista")
        )
        active = str(_get(o, "verksamOrganisation", "kod") or "").upper() == "JA"
        status = "dissolved" if deregistered else "insolvency" if winding_up else "active" if active else "inactive"
        return RegistryCompany(
            company_number=orgnr,
            name=name,
            legal_form=legal_form(_get(o, "juridiskForm", "kod")),
            status=status,
            registered_country="SE",
        )

    def lookup(
        self,
        company_name: str,
        domain: str | None = None,
        company_number: str | None = None,
        country: str | None = None,
    ) -> RegistryCompany | None:
        orgnr = valid_orgnr(company_number)
        if orgnr is None:  # no name search in this API; a personal number is never sent
            return None
        resp = self._http.post(
            f"{API_PATH}/organisationer",
            json={"identitetsbeteckning": orgnr},
            headers={"Authorization": f"Bearer {self._bearer()}"},
        )
        if resp.status_code in (400, 404):
            return None
        found = [
            o
            for o in check(resp).json().get("organisationer") or []
            if isinstance(o, dict)
            and re.sub(r"\D", "", str(_get(o, "organisationsidentitet", "identitetsbeteckning") or "")) == orgnr
        ]
        return self._to_company(found[0], orgnr) if len(found) == 1 else None
