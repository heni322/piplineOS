from __future__ import annotations

from app.adapters.enrichment.base import EnrichedPerson


class MockEnrichment:
    name = "enrichment.mock"

    def __init__(self, people: dict[str, list[EnrichedPerson]] | None = None) -> None:
        self.people = people or {}

    def find_people(self, company_domain: str, persona_titles: list[str]) -> list[EnrichedPerson]:
        wanted = [t.lower() for t in persona_titles]
        return [p for p in self.people.get(company_domain, []) if any(w in p.title.lower() for w in wanted)]
