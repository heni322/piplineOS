from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EnrichedPerson:
    person_name: str
    title: str
    company_domain: str
    country: str | None
    location_text: str | None
    professional_email: str | None
    profile_url: str | None  # only when supplied by a licensed provider; never scraped
    data_source: str  # provider + record id, stored per field
    confidence: float


class EnrichmentProvider(Protocol):
    name: str

    def find_people(self, company_domain: str, persona_titles: list[str]) -> list[EnrichedPerson]: ...
