"""France: the government's free "API Recherche d'entreprises" (recherche-entreprises.api.gouv.fr, no key), built
on the INSEE Sirene register. Used, like Companies House for the UK, only to confirm a company's legal form, which
decides whether the recipient is a company or an individual (sole trader) for the email rules.

Data minimisation: we read the SIREN number, the legal name, the legal-form code and whether the company is active.
Directors' names (`dirigeants`) are never read. A record restricted from public release ("non-diffusible") is treated
as not found, and anything ambiguous returns None (unknown), which the compliance engine blocks.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import httpx

from app.adapters.companies_house.base import RegistryCompany
from app.adapters.http import check, client

BASE_URL = "https://recherche-entreprises.api.gouv.fr"

# INSEE "catégories juridiques" (first digits) -> our normalised legal forms (app.domain.compliance.subscriber).
# Where French law and the UK categories differ, the mapping takes the cautious side (individual = never emailed).
_PREFIX: list[tuple[str, str]] = [
    ("1000", "sole_trader"),  # entrepreneur individuel
    ("2", "partnership"),  # groupements sans personnalité morale (indivision, société en participation...)
    ("3", "other_corporate"),  # personne morale de droit étranger
    ("4", "public_body"),  # personne morale de droit public soumise au droit commercial
    ("51", "other_corporate"),
    ("52", "other_corporate"),  # société en nom collectif (a legal person)
    ("53", "lp"),  # sociétés en commandite: cautious
    ("54", "ltd"),  # SARL, EURL
    ("55", "plc"),  # SA à conseil d'administration
    ("56", "plc"),  # SA à directoire
    ("57", "ltd"),  # SAS, SASU
    ("58", "plc"),  # société européenne
    ("5", "other_corporate"),
    ("6", "other_corporate"),  # sociétés civiles, GIE, coopératives, mutuelles
    ("7", "public_body"),  # personne morale de droit public administratif
    ("8", "other_corporate"),  # organismes privés spécialisés
    ("93", "charity"),  # fondations
    ("9", "other_corporate"),  # associations and other private groupings with legal personality
]
_SUFFIX = re.compile(r"\b(sas|sasu|sarl|eurl|sa|sca|snc|sci|selarl|scop|societe|ste)\b|[^a-z0-9 ]")


def legal_form(code: str | None) -> str:
    c = (code or "").strip()
    if not c.isdigit() or len(c) != 4:
        return "unknown"
    return next((form for prefix, form in _PREFIX if c.startswith(prefix)), "unknown")


def normalise_name(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", _SUFFIX.sub(" ", plain)).strip()


def normalise_siren(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return digits if len(digits) == 9 else None


class FranceRegistry:
    name = "registry.fr.recherche_entreprises"

    def __init__(self, *, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None) -> None:
        self._http = client(base_url, transport)

    def _search(self, q: str) -> list[dict[str, Any]]:
        data = check(self._http.get("/search", params={"q": q, "per_page": 10})).json()
        return [r for r in data.get("results") or [] if isinstance(r, dict)]

    @staticmethod
    def _to_company(r: dict[str, Any]) -> RegistryCompany:
        return RegistryCompany(
            company_number=str(r["siren"]),
            name=str(r.get("nom_raison_sociale") or r.get("nom_complet") or ""),
            legal_form=legal_form(r.get("nature_juridique")),
            status="active" if r.get("etat_administratif") == "A" else "dissolved",
            registered_country="FR",
        )

    @staticmethod
    def _public(r: dict[str, Any]) -> bool:
        return r.get("statut_diffusion", "O") == "O" and bool(r.get("siren"))

    def lookup(
        self,
        company_name: str,
        domain: str | None = None,
        company_number: str | None = None,
        country: str | None = None,
    ) -> RegistryCompany | None:
        siren = normalise_siren(company_number)
        if siren:
            hit = next((r for r in self._search(siren) if str(r.get("siren")) == siren and self._public(r)), None)
            return self._to_company(hit) if hit else None
        want = normalise_name(company_name)
        if not want:
            return None
        matches = [
            r
            for r in self._search(company_name)
            if self._public(r)
            and want
            in {normalise_name(str(r.get("nom_raison_sociale") or "")), normalise_name(str(r.get("nom_complet") or ""))}
        ]
        active = [r for r in matches if r.get("etat_administratif") == "A"] or matches
        return self._to_company(active[0]) if len(active) == 1 else None
