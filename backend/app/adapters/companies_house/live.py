"""Companies House public data API (read-only, free; needs an API key from the developer hub).

Used only to confirm a company's legal form, which decides PECR subscriber type. Anything we can't map with
confidence returns legal_form "unknown", which the compliance engine blocks rather than guesses."""

from __future__ import annotations

import re

import httpx

from app.adapters.companies_house.base import RegistryCompany
from app.adapters.http import check, client

BASE_URL = "https://api.company-information.service.gov.uk"

# Companies House "type" -> our normalised legal form (app.domain.compliance.subscriber).
TYPE_MAP = {
    "ltd": "ltd",
    "private-limited-guarant-nsc": "ltd",
    "private-limited-guarant-nsc-limited-exemption": "ltd",
    "private-limited-shares-section-30-exemption": "ltd",
    "plc": "plc",
    "llp": "llp",
    "limited-partnership": "lp",
    "private-unlimited": "other_corporate",
    "private-unlimited-nsc": "other_corporate",
    "scottish-partnership": "other_corporate",  # a Scottish partnership is a legal person
    "charitable-incorporated-organisation": "charity",
    "scottish-charitable-incorporated-organisation": "charity",
    "royal-charter": "other_corporate",
    "industrial-and-provident-society": "other_corporate",
    "registered-society-non-jurisdictional": "other_corporate",
    "uk-establishment": "other_corporate",
    "oversea-company": "other_corporate",
}
JURISDICTION_COUNTRY = {
    "england-wales": "GB",
    "wales": "GB",
    "england": "GB",
    "scotland": "GB",
    "northern-ireland": "GB",
    "united-kingdom": "GB",
}
_SUFFIX = re.compile(r"\b(limited|ltd\.?|plc|llp|lp|the)\b|[^a-z0-9 ]", re.IGNORECASE)


def normalise_name(name: str) -> str:
    return re.sub(r"\s+", " ", _SUFFIX.sub(" ", name.lower())).strip()


class CompaniesHouseRegistry:
    name = "companies_house.live"

    def __init__(self, api_key: str, *, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise ValueError("Companies House API key missing")
        self._http = client(base_url, transport, auth=(api_key, ""))

    def lookup(
        self,
        company_name: str,
        domain: str | None = None,
        company_number: str | None = None,
        country: str | None = None,
    ) -> RegistryCompany | None:
        """By company number when known (exact). Otherwise an exact (normalised) name match only; several equally
        good matches -> None (ambiguous = unknown)."""
        if company_number:
            resp = self._http.get(f"/company/{company_number}")
            if resp.status_code == 404:
                return None
            return self._profile(company_number, check(resp).json(), company_name)
        items = check(self._http.get("/search/companies", params={"q": company_name, "items_per_page": 10})).json()
        want = normalise_name(company_name)
        matches = [i for i in items.get("items", []) if normalise_name(i.get("title", "")) == want]
        active = [i for i in matches if i.get("company_status") == "active"] or matches
        if len(active) != 1:
            return None
        number = active[0]["company_number"]
        return self._profile(
            number, check(self._http.get(f"/company/{number}")).json(), active[0].get("title", company_name)
        )

    @staticmethod
    def _profile(number: str, profile: dict[str, str], fallback_name: str) -> RegistryCompany:
        return RegistryCompany(
            company_number=number,
            name=profile.get("company_name", fallback_name),
            legal_form=TYPE_MAP.get(profile.get("type", ""), "unknown"),
            status=profile.get("company_status", "unknown"),
            registered_country=JURISDICTION_COUNTRY.get(profile.get("jurisdiction", ""), "unknown"),
        )
