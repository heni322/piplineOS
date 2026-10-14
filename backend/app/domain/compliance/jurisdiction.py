"""Location → jurisdiction parsing.

Safety rules:
- Ireland (IE, EU) and Northern Ireland (GB-NIR, UK) are different jurisdictions.
- Ambiguous or conflicting input returns no country (=> the compliance engine blocks and flags).
- A domain TLD is never used to infer jurisdiction.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

Status = Literal["ok", "unknown", "ambiguous", "conflict"]

# ISO 3166-1 alpha-2 codes we recognise directly (plus common aliases).
COUNTRY_ALIASES: dict[str, str] = {"UK": "GB", "EL": "GR"}
KNOWN_CODES = frozenset(
    [
        "GB",
        "IE",
        "DE",
        "AT",
        "FR",
        "NL",
        "BE",
        "LU",
        "ES",
        "PT",
        "IT",
        "SE",
        "DK",
        "FI",
        "NO",
        "IS",
        "CH",
        "PL",
        "CZ",
        "SK",
        "HU",
        "RO",
        "BG",
        "HR",
        "SI",
        "GR",
        "CY",
        "MT",
        "EE",
        "LV",
        "LT",
        "US",
        "CA",
        "AU",
        "TN",
    ]
)
# EU member states plus the EEA (Iceland, Liechtenstein, Norway): a client established here needs no Article 27
# representative.
EEA = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE IS LI NO".split()  # noqa: SIM905
)
UK_SUBDIVISIONS = frozenset({"GB-ENG", "GB-WLS", "GB-SCT", "GB-NIR"})

# phrase -> (country, subdivision). Longest phrases are matched first.
PHRASES: dict[str, tuple[str, str | None]] = {
    # Northern Ireland (UK) — must be matched before any "ireland" phrase
    "northern ireland": ("GB", "GB-NIR"),
    "n. ireland": ("GB", "GB-NIR"),
    "n ireland": ("GB", "GB-NIR"),
    "belfast": ("GB", "GB-NIR"),
    "derry": ("GB", "GB-NIR"),
    "londonderry": ("GB", "GB-NIR"),
    "lisburn": ("GB", "GB-NIR"),
    "newry": ("GB", "GB-NIR"),
    "armagh": ("GB", "GB-NIR"),
    "county antrim": ("GB", "GB-NIR"),
    "county down": ("GB", "GB-NIR"),
    "co. antrim": ("GB", "GB-NIR"),
    "co. down": ("GB", "GB-NIR"),
    "county tyrone": ("GB", "GB-NIR"),
    "county fermanagh": ("GB", "GB-NIR"),
    # Republic of Ireland (EU)
    "republic of ireland": ("IE", None),
    "eire": ("IE", None),
    "éire": ("IE", None),
    "roi": ("IE", None),
    "dublin": ("IE", None),
    "cork": ("IE", None),
    "galway": ("IE", None),
    "limerick": ("IE", None),
    "waterford": ("IE", None),
    "kilkenny": ("IE", None),
    "county dublin": ("IE", None),
    "co. dublin": ("IE", None),
    "co. cork": ("IE", None),
    "donegal": ("IE", None),
    # Great Britain
    "united kingdom": ("GB", None),
    "great britain": ("GB", None),
    "uk": ("GB", None),
    "u.k.": ("GB", None),
    "england": ("GB", "GB-ENG"),
    "london": ("GB", "GB-ENG"),
    "manchester": ("GB", "GB-ENG"),
    "birmingham": ("GB", "GB-ENG"),
    "leeds": ("GB", "GB-ENG"),
    "bristol": ("GB", "GB-ENG"),
    "liverpool": ("GB", "GB-ENG"),
    "sheffield": ("GB", "GB-ENG"),
    "newcastle upon tyne": ("GB", "GB-ENG"),
    "nottingham": ("GB", "GB-ENG"),
    "reading": ("GB", "GB-ENG"),
    "oxford": ("GB", "GB-ENG"),
    "cambridge": ("GB", "GB-ENG"),
    "brighton": ("GB", "GB-ENG"),
    "milton keynes": ("GB", "GB-ENG"),
    "scotland": ("GB", "GB-SCT"),
    "edinburgh": ("GB", "GB-SCT"),
    "glasgow": ("GB", "GB-SCT"),
    "aberdeen": ("GB", "GB-SCT"),
    "dundee": ("GB", "GB-SCT"),
    "inverness": ("GB", "GB-SCT"),
    "wales": ("GB", "GB-WLS"),
    "cardiff": ("GB", "GB-WLS"),
    "swansea": ("GB", "GB-WLS"),
    "wrexham": ("GB", "GB-WLS"),
    # EU / EEA
    "germany": ("DE", None),
    "deutschland": ("DE", None),
    "berlin": ("DE", None),
    "munich": ("DE", None),
    "münchen": ("DE", None),
    "hamburg": ("DE", None),
    "frankfurt": ("DE", None),
    "cologne": ("DE", None),
    "köln": ("DE", None),
    "düsseldorf": ("DE", None),
    "stuttgart": ("DE", None),
    "austria": ("AT", None),
    "österreich": ("AT", None),
    "vienna": ("AT", None),
    "wien": ("AT", None),
    "france": ("FR", None),
    "paris": ("FR", None),
    "lyon": ("FR", None),
    "marseille": ("FR", None),
    "toulouse": ("FR", None),
    "lille": ("FR", None),
    "netherlands": ("NL", None),
    "the netherlands": ("NL", None),
    "holland": ("NL", None),
    "amsterdam": ("NL", None),
    "rotterdam": ("NL", None),
    "utrecht": ("NL", None),
    "eindhoven": ("NL", None),
    "belgium": ("BE", None),
    "brussels": ("BE", None),
    "antwerp": ("BE", None),
    "spain": ("ES", None),
    "madrid": ("ES", None),
    "barcelona": ("ES", None),
    "portugal": ("PT", None),
    "lisbon": ("PT", None),
    "porto": ("PT", None),
    "italy": ("IT", None),
    "milan": ("IT", None),
    "rome": ("IT", None),
    "sweden": ("SE", None),
    "sverige": ("SE", None),
    "stockholm": ("SE", None),
    "gothenburg": ("SE", None),
    "göteborg": ("SE", None),
    "malmö": ("SE", None),
    "uppsala": ("SE", None),
    "linköping": ("SE", None),
    "denmark": ("DK", None),
    "danmark": ("DK", None),
    "copenhagen": ("DK", None),
    "københavn": ("DK", None),
    "aarhus": ("DK", None),
    "odense": ("DK", None),
    "aalborg": ("DK", None),
    "finland": ("FI", None),
    "suomi": ("FI", None),
    "helsinki": ("FI", None),
    "espoo": ("FI", None),
    "tampere": ("FI", None),
    "vantaa": ("FI", None),
    "oulu": ("FI", None),
    "turku": ("FI", None),
    "jyväskylä": ("FI", None),
    "norway": ("NO", None),
    "norge": ("NO", None),
    "oslo": ("NO", None),
    "trondheim": ("NO", None),
    "stavanger": ("NO", None),
    "tromsø": ("NO", None),
    "drammen": ("NO", None),
    "switzerland": ("CH", None),
    "zurich": ("CH", None),
    "luxembourg": ("LU", None),
    "tunisia": ("TN", None),
    "tunis": ("TN", None),
}

# Words that name places in more than one jurisdiction. Alone, they make the result ambiguous.
AMBIGUOUS = {
    "ireland": ("IE", "GB-NIR (island of Ireland)"),
    "bangor": ("GB-NIR", "GB-WLS"),
    "newport": ("GB-WLS", "GB-ENG"),
    "georgia": ("GE", "US-GA"),
    "ni": ("GB-NIR", "unclear abbreviation"),
}

_SORTED = sorted(PHRASES, key=len, reverse=True)


@dataclass(frozen=True)
class LocationResult:
    country: str | None
    subdivision: str | None
    status: Status
    matches: tuple[str, ...] = field(default=())


def normalise_country_code(code: str | None) -> tuple[str | None, str | None]:
    """'gb' -> ('GB', None); 'GB-NIR' -> ('GB', 'GB-NIR'); 'UK' -> ('GB', None); unknown -> (None, None)."""
    if not code:
        return None, None
    c = code.strip().upper()
    if c in UK_SUBDIVISIONS:
        return "GB", c
    c = COUNTRY_ALIASES.get(c, c)
    return (c, None) if c in KNOWN_CODES else (None, None)


def _norm(text: str) -> str:
    return " " + re.sub(r"[\s,;/|()]+", " ", unicodedata.normalize("NFKC", text).lower()).strip() + " "


def parse_location(text: str | None) -> LocationResult:
    if not text or not text.strip():
        return LocationResult(None, None, "unknown")
    code_country, code_sub = normalise_country_code(text)
    if code_country:
        return LocationResult(code_country, code_sub, "ok", (text.strip(),))

    s = _norm(text)
    found: list[tuple[str, str, str | None]] = []
    for phrase in _SORTED:
        token = f" {phrase} "
        if token in s:
            country, sub = PHRASES[phrase]
            found.append((phrase, country, sub))
            s = s.replace(token, " | ")  # consume, so "northern ireland" doesn't also match "ireland"
    ambiguous = [w for w in AMBIGUOUS if f" {w} " in s]

    if not found:
        if ambiguous:
            return LocationResult(None, None, "ambiguous", tuple(ambiguous))
        return LocationResult(None, None, "unknown")

    countries = {c for _, c, _ in found}
    phrases = tuple(p for p, _, _ in found)
    if len(countries) > 1:
        return LocationResult(None, None, "conflict", phrases)
    country = countries.pop()
    subs = {sub for _, _, sub in found if sub}
    # An ambiguous word that could point elsewhere is ignored only when it agrees with the other evidence.
    if ambiguous and country == "IE" and "ni" in ambiguous:
        return LocationResult(None, None, "conflict", (*phrases, *ambiguous))
    subdivision = subs.pop() if len(subs) == 1 else None
    return LocationResult(country, subdivision, "ok", phrases)
