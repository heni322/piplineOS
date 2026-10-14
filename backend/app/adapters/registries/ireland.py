"""Ireland: the Companies Registration Office's open data ("Company Records", opendata.cro.ie, CC BY 4.0, updated
daily, free). It is a downloadable file, so the owner imports it (`pipelineos registers import ie <zip>`) and
lookups read the local copy, with no network call. Attribution required by the licence: "Contains Irish Public
Sector Data licensed under a Creative Commons Attribution 4.0 International (CC BY 4.0) licence."

The file holds companies only (business names and sole traders are a separate register and are not in it), so a
sole trader stays "unknown", which blocks. We keep the number, name, type and status; addresses are not imported.
Company types are mapped from the type's text; anything unrecognised is "unknown" (blocked).
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy.engine import Engine

from app.adapters.registries.local_copy import CopyRow, CopyWriter, ImportReport, LocalCopyRegistry

# (keyword in the CRO company type, normalised legal form). First match wins.
TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("limited partnership", "lp"),
    ("partnership", "partnership"),
    ("public limited", "plc"),
    ("plc", "plc"),
    ("unlimited", "other_corporate"),
    ("designated activity", "ltd"),
    ("dac", "ltd"),
    ("limited by guarantee", "other_corporate"),
    ("clg", "other_corporate"),
    ("limited by shares", "ltd"),
    ("private company", "ltd"),
    ("ltd", "ltd"),
    ("societas europaea", "plc"),
    ("european company", "plc"),
    ("external", "other_corporate"),
    ("foreign", "other_corporate"),
    ("investment company", "other_corporate"),
]
_SUFFIX = re.compile(
    r"\b(limited|ltd|plc|dac|clg|uc|unlimited company|designated activity company|teoranta|teo|cpt|the)\b|[^a-z0-9 ]"
)


def legal_form_from_type(company_type: str) -> str:
    t = f" {company_type.strip().lower()} "
    return next((form for key, form in TYPE_KEYWORDS if re.search(rf"\b{re.escape(key)}\b", t)), "unknown")


def valid_company_number(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return str(int(digits)) if digits and len(digits) <= 7 and int(digits) > 0 else None


def normalise_name(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", _SUFFIX.sub(" ", plain)).strip()


def _rows(source: Path) -> Iterator[dict[str, str]]:
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as z:
            member = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            with z.open(member) as raw:
                yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
    else:
        with source.open(encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f)


def import_cro(engine: Engine, source: Path) -> ImportReport:
    """Owner-run import of the CRO "Company Records" open-data file (companies.csv or its zip)."""
    skipped = 0
    with engine.begin() as conn:
        w = CopyWriter(conn, "IE", None, normalise_name)
        names: list[tuple[str, str]] = []
        for r in _rows(source):
            number = valid_company_number(r.get("company_num"))
            if number is None or "company_type" not in r or "company_status" not in r:
                skipped += 1
                continue
            active = (r.get("company_status") or "").strip().lower() == "normal"
            form = legal_form_from_type(r.get("company_type") or "")
            w.company(CopyRow(number, (r.get("company_type_code") or None), form, "active" if active else "not_active"))
            names.append((number, r.get("company_name") or ""))
        w.flush_companies()
        for number, name in names:
            w.name(number, name)
        gone = w.finish()
    return ImportReport("IE", None, len(w.seen), skipped, w.names, gone)


def IrelandRegistry(engine: Engine) -> LocalCopyRegistry:  # noqa: N802 - reads like the other register classes
    return LocalCopyRegistry(engine, "IE", "registry.ie.cro_open_data", normalise_name, valid_company_number)
