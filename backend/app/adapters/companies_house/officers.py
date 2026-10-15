"""Companies House officers (read-only, free; the same API key as the register lookup).

Two uses, both from the public register instead of scraped social profiles:
- **Leadership-change signals** (`OfficerAppointmentSource`): a watched company appointed a new director. The
  signal says which role and when; it never carries the person's name.
- **Decision-makers** (`CompaniesHouseOfficers`, an enrichment provider): the company's current directors, by name
  and stated occupation. The register gives no email address, so these contacts arrive without one: the pipeline
  then offers the alternative channels (or a contact the client supplies) instead of guessing an address.

What we take from an appointment, and nothing more (data minimisation): name, role, stated occupation,
appointment date and country of residence (country only: the compliance engine needs the person's jurisdiction).
Date of birth, nationality and addresses are never read.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any

import httpx

from app.adapters.companies_house.live import BASE_URL, normalise_name
from app.adapters.enrichment.base import EnrichedPerson
from app.adapters.http import check, client
from app.adapters.signals.base import RawSignal
from app.adapters.signals.ocds import normalise_company_number

REGISTER_PAGE = "https://find-and-update.company-information.service.gov.uk/company/{n}/officers"
# natural persons who run the company; corporate directors and secretaries are not decision-makers
PERSON_ROLES = {"director", "llp-designated-member", "llp-member", "managing-officer", "member-of-a-management-organ"}
_GENERIC_OCCUPATION = re.compile(r"^(company )?director$|^none$|^n/?a$|^-$", re.IGNORECASE)


@dataclass(frozen=True)
class Officer:
    name: str
    role: str
    occupation: str
    appointed_on: date
    country: str  # country of residence as the register states it ("England", "France"...), or ""
    ref: str  # hash of the register's appointment link: stable, not the officer's id


def display_name(register_name: str) -> str:
    """ "SMITH, John Andrew" -> "John Andrew Smith"; names without a comma are kept as given."""
    surname, sep, forenames = register_name.partition(",")
    if not sep:
        return register_name.strip()
    return f"{forenames.strip()} {surname.strip().title()}".strip()


def job_title(officer: Officer) -> str:
    occ = officer.occupation.strip()
    if occ and not _GENERIC_OCCUPATION.match(occ):
        return occ if "director" in occ.lower() else f"Director ({occ})"
    return "Director" if officer.role == "director" else officer.role.replace("-", " ").title()


class _Officers:
    def __init__(self, api_key: str, *, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise ValueError("Companies House API key missing")
        self._http = client(base_url, transport, auth=(api_key, ""))

    def current(self, company_number: str) -> list[Officer]:
        """Active (not resigned) natural-person officers, newest appointment first."""
        resp = self._http.get(
            f"/company/{company_number}/officers",
            params={"items_per_page": 100, "order_by": "appointed_on", "register_view": "false"},
        )
        if resp.status_code == 404:
            return []
        out = []
        for item in check(resp).json().get("items") or []:
            if item.get("resigned_on") or item.get("officer_role") not in PERSON_ROLES or not item.get("name"):
                continue
            try:
                appointed = date.fromisoformat(str(item.get("appointed_on")))
            except ValueError:
                continue
            link = str((item.get("links") or {}).get("self") or f"{company_number}:{item.get('name')}")
            out.append(
                Officer(
                    name=display_name(str(item["name"])),
                    role=str(item["officer_role"]),
                    occupation=str(item.get("occupation") or ""),
                    appointed_on=appointed,
                    country=str(item.get("country_of_residence") or ""),
                    ref=hashlib.sha256(link.encode()).hexdigest()[:16],
                )
            )
        return sorted(out, key=lambda o: o.appointed_on, reverse=True)

    def resolve_number(self, company_name: str) -> str | None:
        """Exact (normalised) name match among active companies only; ambiguous -> None, as the register lookup."""
        items = check(self._http.get("/search/companies", params={"q": company_name, "items_per_page": 10})).json()
        want = normalise_name(company_name)
        matches = [
            i
            for i in items.get("items") or []
            if normalise_name(i.get("title", "")) == want and i.get("company_status") == "active"
        ]
        return str(matches[0]["company_number"]) if len(matches) == 1 else None


@dataclass(frozen=True)
class WatchedCompany:
    company_name: str
    company_number: str
    domain: str | None = None


class OfficerAppointmentSource(_Officers):
    """New directors at the watched UK companies since `since`: one signal per appointment. The query is unused
    (the watchlist is the query); it is accepted to fit the signal-source interface."""

    name = "companies_house.officers"

    def __init__(self, watched: list[WatchedCompany], api_key: str, **kw: Any) -> None:
        super().__init__(api_key, **kw)
        self._watched = watched

    def fetch(self, query: str, since: datetime) -> list[RawSignal]:
        out = []
        for w in self._watched:
            number = normalise_company_number(w.company_number)
            if number is None:
                continue
            for o in self.current(number):
                when = datetime.combine(o.appointed_on, time(9, 0), tzinfo=UTC)
                if when < since:
                    break  # newest first
                role = job_title(o)
                out.append(
                    RawSignal(
                        company_name=w.company_name,
                        domain=w.domain,
                        country_hint="GB",
                        signal_type="leadership_change",
                        title=f"{w.company_name} appointed a new {role.lower()}"[:200],
                        text=(
                            f"According to the Companies House register, {w.company_name} appointed a new "
                            f"{role} on {o.appointed_on.isoformat()}."
                        ),
                        source_url=REGISTER_PAGE.format(n=number),
                        detected_at=when,
                        source_record_id=f"ch-officer:{number}:{o.ref}",
                        company_number=number,
                    )
                )
        return out


class CompaniesHouseOfficers(_Officers):
    """Enrichment from the public register: the current directors of a UK company, with no email address."""

    name = "companies_house.officers"

    def find_people(
        self,
        company_domain: str,
        persona_titles: list[str],
        company_name: str | None = None,
        *,
        company_number: str | None = None,
    ) -> list[EnrichedPerson]:
        number = normalise_company_number(company_number or "") or (
            self.resolve_number(company_name) if company_name else None
        )
        if number is None:
            return []
        return [
            EnrichedPerson(
                person_name=o.name,
                title=job_title(o),
                company_domain=company_domain,
                country="GB",
                location_text=o.country or None,  # country only; unknown -> the engine blocks
                professional_email=None,  # the register gives none, and we never guess one
                profile_url=None,
                data_source=f"companies_house:officers:{number}",
                confidence=0.9,
            )
            for o in self.current(number)
        ]
