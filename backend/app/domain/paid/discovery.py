"""New target companies from a Google Places search ("IT support Leeds", "agence web Lyon").

For each business found with a website:
1. the site must print the company's register number (UK company number, or French SIREN/SIRET);
2. the register must confirm that number: an active company, whose registered name matches the site's business;
3. the site must then pass the normal website check (number on its pages, generic business address on its own
   domain, mail server).

What is kept is ours, not Google's: the register's name and number, the verified domain and the published address.
Nothing Places returned is stored (Google's terms), and personal addresses are never taken.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.adapters.companies_house.base import CompaniesRegistry
from app.adapters.companies_house.live import normalise_name
from app.adapters.website_contacts import WebsiteFinder
from app.domain.paid.places import GatedPlaces


@dataclass(frozen=True)
class Discovered:
    name: str  # as registered
    company_number: str
    domain: str
    address: str | None  # the generic business address the site publishes, if any
    country: str


@dataclass
class DiscoveryReport:
    searched: int = 0
    with_website: int = 0
    no_number_on_site: int = 0
    not_confirmed_by_register: int = 0
    site_check_failed: int = 0
    stopped_early: bool = False  # the time limit was reached: the remaining businesses were not checked
    found: list[Discovered] = field(default_factory=list)


def _same_business(a: str, b: str) -> bool:
    wa, wb = set(normalise_name(a).split()), set(normalise_name(b).split())
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.5


def discover_companies(
    places: GatedPlaces,
    finder: WebsiteFinder,
    registry: CompaniesRegistry,
    query: str,
    *,
    country: str,
    pages: int = 1,
    time_limit: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> DiscoveryReport:
    """`time_limit` (seconds): stop checking websites after it, keeping what was confirmed so far. The search itself
    is billed once per page whatever happens next."""
    report = DiscoveryReport()
    seen: set[str] = set()
    deadline = None if time_limit is None else clock() + time_limit
    for hit in places.discover(query, pages):
        if deadline is not None and clock() > deadline:
            report.stopped_early = True
            break
        report.searched += 1
        if not hit.website or hit.website in seen:
            continue
        seen.add(hit.website)
        report.with_website += 1
        number = finder.identify(hit.website, country)
        if number is None:
            report.no_number_on_site += 1
            continue
        company = registry.lookup(hit.name, domain=hit.website, company_number=number, country=country)
        if (
            company is None
            or company.company_number.lstrip("0") != number.lstrip("0")
            or company.status != "active"
            or not _same_business(company.name, hit.name)
        ):
            report.not_confirmed_by_register += 1
            continue
        site = finder.check_site(hit.website, number)
        if site is None:
            report.site_check_failed += 1
            continue
        report.found.append(Discovered(company.name, company.company_number, site.domain, site.address, country))
    return report
