"""Belgium: the Crossroads Bank for Enterprises (KBO/BCE) open data, published by FPS Economy as free files after a
free registration. There is no free lookup API, so the owner downloads the monthly full extract and imports it
(`pipelineos registers import be <zip>`); lookups then read the local copy, with no network call.

Data minimisation:
- Only LEGAL PERSONS are imported. A natural person's entry (a sole trader, whose name is their own) is skipped, so
  it is never stored and the lookup returns "unknown", which blocks.
- Only the enterprise number, the official names, the legal form and the status are kept. Establishment addresses,
  activities and contact details in the extract are not imported.

Legal forms are mapped from the official descriptions in the extract's own code table (`code.csv`), not from
hard-coded numbers. A form whose description matches nothing below is "unknown" (blocked).
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import Engine

from app.adapters.registries.local_copy import CopyRow, CopyWriter, ImportReport, LocalCopyRegistry

# (keyword in the Dutch or French description, normalised legal form). Order matters: first match wins.
FORM_KEYWORDS: list[tuple[str, str]] = [
    ("eenmanszaak", "sole_trader"),
    ("entreprise individuelle", "sole_trader"),
    ("commanditaire vennootschap", "lp"),
    ("société en commandite", "lp"),
    ("vennootschap onder firma", "partnership"),
    ("société en nom collectif", "partnership"),
    ("maatschap", "partnership"),
    ("société simple", "partnership"),
    ("publiekrechtelijk", "public_body"),
    ("droit public", "public_body"),
    ("gemeente", "public_body"),
    ("commune", "public_body"),
    ("ocmw", "public_body"),
    ("cpas", "public_body"),
    ("provincie", "public_body"),
    ("intercommunale", "public_body"),
    ("coöperatieve", "other_corporate"),
    ("cooperatieve", "other_corporate"),
    ("coopérative", "other_corporate"),
    ("naamloze vennootschap", "plc"),
    ("société anonyme", "plc"),
    ("europese vennootschap", "plc"),
    ("société européenne", "plc"),
    ("besloten vennootschap", "ltd"),
    ("responsabilité limitée", "ltd"),
    ("economisch samenwerkingsverband", "other_corporate"),
    ("groupement d'intérêt économique", "other_corporate"),
    ("vereniging zonder winstoogmerk", "other_corporate"),
    ("association sans but lucratif", "other_corporate"),
    ("stichting", "charity"),
    ("fondation", "charity"),
    ("buitenlands", "other_corporate"),
    ("étranger", "other_corporate"),
]
_SUFFIX = re.compile(r"\b(nv|sa|bv|srl|bvba|sprl|cv|sc|cvba|scrl|vzw|asbl|comm ?v|scs|vof|snc|se)\b|[^a-z0-9 ]")


def legal_form_from_description(description: str) -> str:
    d = description.strip().lower()
    return next((form for key, form in FORM_KEYWORDS if key in d), "unknown")


def valid_enterprise_number(value: str | None) -> str | None:
    """Ten digits starting with 0 or 1 (a nine-digit number gets its leading 0 back); the last two digits are
    97 minus the first eight modulo 97."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 9:
        digits = "0" + digits
    if len(digits) != 10 or digits[0] not in "01":
        return None
    return digits if 97 - int(digits[:8]) % 97 == int(digits[8:]) else None


def normalise_name(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", _SUFFIX.sub(" ", plain.replace(".", ""))).strip()


# ------------------------------------------------------------------------------------------------------ import
def _rows(z: zipfile.ZipFile | None, folder: Path | None, name: str) -> Iterator[dict[str, str]]:
    if z is not None:
        with z.open(name) as raw:
            yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
    elif folder is not None:
        with (folder / name).open(encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f)


def import_kbo(engine: Engine, source: Path) -> ImportReport:
    """Owner-run import of a KBO open-data full extract (zip or unzipped folder). Legal persons only."""
    z = zipfile.ZipFile(source) if source.suffix.lower() == ".zip" else None
    folder = None if z else source
    codes = list(_rows(z, folder, "code.csv"))
    meta = {r.get("Variable"): r.get("Value") for r in _rows(z, folder, "meta.csv")}
    extract = datetime.strptime(str(meta["ExtractDate"]), "%d-%m-%Y").date() if meta.get("ExtractDate") else None
    legal_type = {
        c["Code"]
        for c in codes
        if c.get("Category") == "TypeOfEnterprise"
        and any(k in c.get("Description", "").lower() for k in ("rechtspersoon", "personne morale"))
    }
    if len(legal_type) != 1:
        raise ValueError("cannot tell legal persons from natural persons in code.csv; refusing to import")
    forms: dict[str, str] = {}
    for c in codes:
        if c.get("Category") == "JuridicalForm":
            mapped = legal_form_from_description(c.get("Description", ""))
            if mapped != "unknown" or c["Code"] not in forms:
                forms[c["Code"]] = mapped

    skipped = 0
    with engine.begin() as conn:
        w = CopyWriter(conn, "BE", extract, normalise_name)
        for e in _rows(z, folder, "enterprise.csv"):
            number = valid_enterprise_number(e.get("EnterpriseNumber"))
            if e.get("TypeOfEnterprise") not in legal_type or number is None:
                skipped += 1  # natural persons are never stored
                continue
            code = e.get("JuridicalForm") or ""
            active = e.get("Status") == "AC" and e.get("JuridicalSituation") == "000"
            w.company(CopyRow(number, code or None, forms.get(code, "unknown"), "active" if active else "not_active"))
        w.flush_companies()
        for d in _rows(z, folder, "denomination.csv"):
            if d.get("TypeOfDenomination") == "001":  # official names only (not trade names)
                number = valid_enterprise_number(d.get("EntityNumber"))
                if number:
                    w.name(number, d.get("Denomination") or "")
        gone = w.finish()
    return ImportReport("BE", extract, len(w.seen), skipped, w.names, gone)


def BelgiumRegistry(engine: Engine) -> LocalCopyRegistry:  # noqa: N802 - reads like the other register classes
    return LocalCopyRegistry(engine, "BE", "registry.be.kbo_open_data", normalise_name, valid_enterprise_number)
