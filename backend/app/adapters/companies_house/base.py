from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RegistryCompany:
    company_number: str
    name: str
    legal_form: str  # normalised: ltd | plc | llp | lp | ...
    status: str  # active | dissolved | ...
    registered_country: str  # GB


class CompaniesRegistry(Protocol):
    name: str

    def lookup(
        self, company_name: str, domain: str | None = None, company_number: str | None = None
    ) -> RegistryCompany | None: ...
