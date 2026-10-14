"""One registry per country: the company's country decides which official register confirms its legal form.
A country without a register adapter returns None (unknown), which the compliance engine blocks."""

from __future__ import annotations

from app.adapters.companies_house.base import CompaniesRegistry, RegistryCompany


class RoutingRegistry:
    name = "registry.routing"

    def __init__(self, by_country: dict[str, CompaniesRegistry], default_country: str = "GB") -> None:
        self.by_country = {k.upper(): v for k, v in by_country.items()}
        self.default_country = default_country

    def lookup(
        self,
        company_name: str,
        domain: str | None = None,
        company_number: str | None = None,
        country: str | None = None,
    ) -> RegistryCompany | None:
        reg = self.by_country.get((country or self.default_country).upper())
        if reg is None:
            return None
        return reg.lookup(company_name, domain, company_number, country)

    def name_for(self, country: str | None) -> str:
        reg = self.by_country.get((country or self.default_country).upper())
        return reg.name if reg else "none"
