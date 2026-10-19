"""What is built for each country, and what is still needed before its prospects can be emailed. Read-only: it
signs, enables and configures nothing. Shown on the Go-live page next to the legal status of each country.

Each "still needed" item says who acts: the owner, a lawyer, a native speaker, or per client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.i18n import BY_PREFIX, CATALOGUE
from app.settings import Settings

RegisterKind = Literal["companies_house", "free", "credentials", "import", "none"]
REGISTERS: dict[str, tuple[RegisterKind, str]] = {
    "GB": ("companies_house", "Companies House (free API key)"),
    "FR": ("free", "Sirene / Recherche d'entreprises (free, no key)"),
    "NO": ("free", "Brønnøysund Enhetsregisteret (free, no key)"),
    "FI": ("free", "PRH YTJ open data (free, no key)"),
    "SE": ("credentials", "Bolagsverket (free, credentials on registration)"),
    "DK": ("credentials", "CVR, Erhvervsstyrelsen (free, credentials on request)"),
    "BE": ("import", "KBO/BCE open data (free file, after registration)"),
    "IE": ("import", "CRO open data (free file, CC BY 4.0)"),
}
# reply-safety test sets, by language prefix (app/evals/datasets)
SAFETY_SETS = {
    "en": "English",
    "fr": "French",
    "nb": "Nordic",
    "no": "Nordic",
    "da": "Nordic",
    "fi": "Nordic",
    "sv": "Nordic",
    "es": "Stage 4",
    "it": "Stage 4",
    "pt": "Stage 4",
    "nl": "Stage 4",
}

Who = Literal["you", "lawyer", "native speaker", "each client"]


@dataclass
class Needed:
    who: Who
    what: str


@dataclass
class CountryBuild:
    register_kind: RegisterKind
    register_name: str
    register_ready: bool
    register_detail: str
    languages_with_texts: list[str]
    languages_in_english: list[str]
    safety_tested: list[str]
    still_needed: list[Needed] = field(default_factory=list)
    note: str | None = None


def _register(conn: Connection, settings: Settings, cc: str) -> tuple[RegisterKind, str, bool, str]:
    kind, name = REGISTERS.get(cc, ("none", "no free register that confirms legal form"))
    if kind == "companies_house":
        on = settings.companies_house_adapter_mode != "mock" and settings.companies_house_api_key is not None
        return kind, name, on, "switched on" if on else "API key not set"
    if kind == "none":
        return kind, name, False, "companies stay unknown (blocked) until a paid source is chosen"
    mode = getattr(settings, f"{cc.lower()}_registry_adapter_mode", "mock")
    if kind == "free":
        return kind, name, mode != "mock", "switched on" if mode != "mock" else "not switched on yet"
    if kind == "credentials":
        creds = (
            bool(settings.bolagsverket_client_id and settings.bolagsverket_client_secret)
            if cc == "SE"
            else bool(settings.cvr_username and settings.cvr_password)
        )
        on = mode != "mock" and creds
        return kind, name, on, "switched on" if on else ("credentials set" if creds else "credentials not set")
    n, latest = conn.execute(
        text("SELECT count(*), max(imported_at) FROM register_companies WHERE country_code = :c"), {"c": cc}
    ).one()
    on = mode != "mock" and n > 0
    detail = f"{n} companies imported {latest:%d %b %Y}" if n else "file not imported"
    return kind, name, on, detail + ("" if mode != "mock" else ", not switched on")


def country_build(conn: Connection, settings: Settings, row: dict[str, object]) -> CountryBuild:
    cc = str(row["country_code"])
    kind, name, ready, detail = _register(conn, settings, cc)
    langs = [str(x) for x in (row.get("languages") or [])]  # type: ignore[attr-defined]
    with_texts = [lg for lg in langs if lg[:2] in BY_PREFIX and BY_PREFIX[lg[:2]] in CATALOGUE]
    english_only = [lg for lg in langs if lg not in with_texts]
    tested = sorted({SAFETY_SETS[lg[:2]] for lg in langs if lg[:2] in SAFETY_SETS} | {"English"})
    b = CountryBuild(kind, name, ready, detail, with_texts, english_only, tested)

    if row.get("mode") is None:
        b.still_needed.append(Needed("lawyer", "write the country's rules (none yet)"))
    elif not row.get("verified_by_counsel"):
        b.still_needed.append(Needed("lawyer", "review the country's email rules and the footer"))
    if row.get("mode") == "consent_required":
        b.note = "Email needs prior consent here: prospects are offered other channels only, never cold email."
    elif not ready:
        b.still_needed.append(
            {
                "companies_house": Needed("you", "set the free Companies House API key and switch it on"),
                "free": Needed("you", "switch the free register on (no key needed)"),
                "credentials": Needed("you", "apply for the free register credentials, then switch it on"),
                "import": Needed("you", "download and import the register file, then switch it on"),
                "none": Needed("you", "choose a paid register source (no free one confirms legal form)"),
            }[kind]
        )
    if any(not lg.startswith("en") for lg in with_texts):
        b.still_needed.append(Needed("native speaker", "check the fixed texts and footer wording"))
    if row.get("eu_representative_required"):
        b.still_needed.append(Needed("each client", "outside the EU/EEA: record an EU representative"))
    if not row.get("enabled"):
        b.still_needed.append(Needed("you", "enable the country here (authenticator code)"))
    return b
