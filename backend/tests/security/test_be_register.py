"""Belgium and Ireland (Stage 4): the owner imports the register's open-data file; only legal persons are stored
(a sole trader's entry, which is a person's name, never is); lookups read the local copy; a re-import never
deletes anything; the application account can read but not write the copy."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.adapters.registries.belgium import BelgiumRegistry, import_kbo
from app.db.session import get_engine

pytestmark = pytest.mark.db
CODES = [
    ("TypeOfEnterprise", "1", "NL", "Natuurlijk persoon"),
    ("TypeOfEnterprise", "2", "NL", "Rechtspersoon"),
    ("JuridicalForm", "014", "NL", "Naamloze vennootschap"),
    ("JuridicalForm", "610", "NL", "Besloten vennootschap"),
    ("JuridicalForm", "610", "FR", "Société à responsabilité limitée"),
    ("JuridicalForm", "017", "NL", "Vereniging zonder winstoogmerk"),
]


def _csv(header: list[str], rows: list[tuple[str, ...]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def extract(path: Path, enterprises: list[tuple[str, ...]], names: list[tuple[str, ...]], day: str) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("meta.csv", _csv(["Variable", "Value"], [("ExtractDate", day)]))
        z.writestr("code.csv", _csv(["Category", "Code", "Language", "Description"], CODES))
        z.writestr(
            "enterprise.csv",
            _csv(
                ["EnterpriseNumber", "Status", "JuridicalSituation", "TypeOfEnterprise", "JuridicalForm",
                 "JuridicalFormCAC", "StartDate"],
                enterprises,
            ),
        )  # fmt: skip
        z.writestr("denomination.csv", _csv(["EntityNumber", "Language", "TypeOfDenomination", "Denomination"], names))
    return path


ACME = ("0403.170.701", "AC", "000", "2", "014", "", "01-01-1990")
BAKKER = ("0500.000.059", "AC", "000", "1", "", "", "01-01-2010")  # a sole trader: a natural person
VZW = ("0412.345.614", "AC", "000", "2", "017", "", "01-01-2000")
NAMES = [
    ("0403.170.701", "2", "001", "Schelde Data NV"),
    ("0403.170.701", "1", "001", "Schelde Data SA"),
    ("0403.170.701", "2", "003", "Schelde Shop"),  # a trade name: not imported
    ("0500.000.059", "2", "001", "Jan Peeters"),
    ("0412.345.614", "2", "001", "Vrienden van de Leie"),
]


def test_import_keeps_legal_persons_only_and_lookups_use_the_local_copy(clean_db, settings, tmp_path) -> None:  # type: ignore[no-untyped-def]
    owner = get_engine(settings.database_owner_url)
    first = extract(tmp_path / "kbo1.zip", [ACME, BAKKER, VZW], NAMES, "01-10-2026")
    r = import_kbo(owner, first)
    assert (r.companies, r.skipped, r.names, r.marked_not_in_extract) == (2, 1, 3, 0)
    with owner.connect() as conn:
        stored = " ".join(conn.execute(text("SELECT string_agg(name, ' ') FROM register_names")).scalar_one().split())
        assert "Peeters" not in stored and "Shop" not in stored  # the person and the trade name were never stored
        assert (
            conn.execute(text("SELECT count(*) FROM register_companies WHERE company_number = '0500000059'")).scalar()
            == 0
        )

    reg = BelgiumRegistry(clean_db)
    acme = reg.lookup("x", company_number="0403170701")
    assert acme is not None and (acme.legal_form, acme.status, acme.registered_country) == ("plc", "active", "BE")
    assert reg.lookup("Schelde Data SA") is not None and reg.lookup("schelde data") is not None  # either language
    assert reg.lookup("Jan Peeters") is None and reg.lookup("x", company_number="0500.000.059") is None  # unknown
    assert reg.lookup("x", company_number="0412345614").legal_form == "other_corporate"  # type: ignore[union-attr]

    # next month: ACME is gone from the extract and the association went bankrupt; nothing is deleted
    second = extract(tmp_path / "kbo2.zip", [("0412.345.614", "AC", "012", "2", "017", "", "")], NAMES, "01-11-2026")
    r = import_kbo(owner, second)
    assert r.marked_not_in_extract == 1
    assert reg.lookup("x", company_number="0403170701").status == "not_in_extract"  # type: ignore[union-attr]
    assert reg.lookup("x", company_number="0412345614").status == "not_active"  # type: ignore[union-attr]

    with clean_db.connect() as conn, pytest.raises(ProgrammingError):  # the application account cannot write it
        conn.execute(text("UPDATE register_companies SET status = 'active'"))


def test_import_refuses_an_extract_it_cannot_read_safely(clean_db, settings, tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "odd.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("meta.csv", _csv(["Variable", "Value"], []))
        z.writestr("code.csv", _csv(["Category", "Code", "Language", "Description"], CODES[2:]))
        z.writestr("enterprise.csv", _csv(["EnterpriseNumber", "TypeOfEnterprise"], [("0403.170.701", "2")]))
        z.writestr("denomination.csv", _csv(["EntityNumber", "Language", "TypeOfDenomination", "Denomination"], []))
    with pytest.raises(ValueError, match="legal persons"):
        import_kbo(get_engine(settings.database_owner_url), path)


def test_ireland_import_and_lookup(clean_db, settings, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.adapters.registries.ireland import IrelandRegistry, import_cro

    header = ["company_num", "company_name", "company_status_code", "company_status", "company_type_code",
              "company_type", "company_address_1"]  # fmt: skip
    rows = [
        ("612345", "Liffey Cloud Services Limited", "1", "Normal", "1", "LTD - Private Company Limited by Shares",
         "1 Quay St"),
        ("98765", "Burren Data DAC", "1", "Normal", "11", "DAC - Designated Activity Company", "2 Main St"),
        ("55555", "Old Harbour Ltd", "6", "Dissolved", "1", "LTD - Private Company Limited by Shares", "3 Pier Rd"),
        ("44444", "Mystery Entity", "1", "Normal", "99", "Something New", "4 Lane"),
    ]  # fmt: skip
    path = tmp_path / "companies.csv.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("companies.csv", _csv(header, rows))
    r = import_cro(get_engine(settings.database_owner_url), path)
    assert (r.companies, r.names, r.marked_not_in_extract) == (4, 4, 0)
    with get_engine(settings.database_owner_url).connect() as conn:  # addresses are not imported
        cols = {c for (c,) in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name LIKE 'register_%'"))}  # fmt: skip
        assert not any("address" in c for c in cols)

    reg = IrelandRegistry(clean_db)
    liffey = reg.lookup("Liffey Cloud Services Ltd")
    assert liffey is not None and (liffey.company_number, liffey.legal_form, liffey.status) == (
        "612345",
        "ltd",
        "active",
    )
    assert reg.lookup("x", company_number="098765").legal_form == "ltd"  # type: ignore[union-attr]
    assert reg.lookup("Old Harbour").status == "not_active"  # type: ignore[union-attr]
    assert reg.lookup("Mystery Entity").legal_form == "unknown"  # type: ignore[union-attr]
    assert reg.lookup("Nobody Limited") is None
