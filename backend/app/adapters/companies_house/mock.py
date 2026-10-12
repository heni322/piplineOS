from __future__ import annotations

from app.adapters.companies_house.base import RegistryCompany


class MockCompaniesHouse:
    """Synthetic registry. Unknown names return None (=> subscriber type unknown => blocked)."""

    name = "companies_house.mock"

    def __init__(self, records: dict[str, RegistryCompany] | None = None) -> None:
        self.records = {k.lower(): v for k, v in (records or {}).items()}

    def lookup(self, company_name: str, domain: str | None = None) -> RegistryCompany | None:
        return self.records.get(company_name.lower())
