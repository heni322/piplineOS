"""Registers published as free open-data FILES (no lookup API): the owner imports the file, lookups read the local
copy (tables `register_companies` / `register_names`, migration 0013), and no network call is made at lookup time.

Shared by Belgium (KBO/BCE) and Ireland (CRO). Each country's module decides what to import (legal persons only)
and how to map legal forms; this module stores and reads. Imports refresh rows and mark companies that left the
extract as `not_in_extract`; they never delete anything.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.adapters.companies_house.base import RegistryCompany


@dataclass(frozen=True)
class CopyRow:
    number: str
    form_code: str | None
    legal_form: str
    status: str  # active | not_active | ...


@dataclass(frozen=True)
class ImportReport:
    country: str
    extract_date: date | None
    companies: int
    skipped: int
    names: int
    marked_not_in_extract: int


class CopyWriter:
    """Batched upserts inside one transaction; call `finish()` to mark companies missing from this extract."""

    def __init__(self, conn: Connection, country: str, extract: date | None, normalise: Callable[[str], str]) -> None:
        self.conn, self.country, self.extract, self.normalise = conn, country, extract, normalise
        self.seen: set[str] = set()
        self.names = 0
        self._rows: list[dict[str, object]] = []
        self._names: list[dict[str, object]] = []
        conn.execute(text("CREATE TEMP TABLE register_seen (n text PRIMARY KEY) ON COMMIT DROP"))

    def company(self, row: CopyRow) -> None:
        self.seen.add(row.number)
        self._rows.append(
            {
                "c": self.country,
                "n": row.number,
                "fc": row.form_code,
                "f": row.legal_form,
                "s": row.status,
                "d": self.extract,
            }
        )
        if len(self._rows) >= 5000:
            self._flush_companies()

    def name(self, number: str, name: str) -> None:
        norm = self.normalise(name)
        if number in self.seen and name.strip() and norm:
            self._names.append({"c": self.country, "n": number, "name": name.strip(), "norm": norm})
            if len(self._names) >= 5000:
                self._flush_names()

    def _flush_companies(self) -> None:
        if self._rows:
            self.conn.execute(
                text("""INSERT INTO register_companies (country_code, company_number, form_code, legal_form, status,
                          extract_date, imported_at)
                        VALUES (:c, :n, :fc, :f, :s, :d, now())
                        ON CONFLICT (country_code, company_number) DO UPDATE SET form_code = excluded.form_code,
                          legal_form = excluded.legal_form, status = excluded.status,
                          extract_date = excluded.extract_date, imported_at = now()"""),
                self._rows,
            )
            self.conn.execute(text("INSERT INTO register_seen (n) VALUES (:n) ON CONFLICT DO NOTHING"), self._rows)
            self._rows.clear()

    def _flush_names(self) -> None:
        if self._names:
            self.conn.execute(
                text("""INSERT INTO register_names (country_code, company_number, name, name_norm)
                        VALUES (:c, :n, :name, :norm) ON CONFLICT DO NOTHING"""),
                self._names,
            )
            self.names += len(self._names)
            self._names.clear()

    def flush_companies(self) -> None:
        self._flush_companies()

    def finish(self) -> int:
        self._flush_companies()
        self._flush_names()
        return int(
            self.conn.execute(
                text("""UPDATE register_companies SET status = 'not_in_extract'
                        WHERE country_code = :c AND status <> 'not_in_extract'
                          AND company_number NOT IN (SELECT n FROM register_seen)"""),
                {"c": self.country},
            ).rowcount
        )


class LocalCopyRegistry:
    """Lookup by valid number, or by exact normalised name (unique, preferring active)."""

    def __init__(
        self,
        engine: Engine,
        country: str,
        name: str,
        normalise: Callable[[str], str],
        valid_number: Callable[[str | None], str | None],
    ) -> None:
        self._engine, self.country, self.name = engine, country, name
        self._normalise, self._valid = normalise, valid_number

    def lookup(
        self,
        company_name: str,
        domain: str | None = None,
        company_number: str | None = None,
        country: str | None = None,
    ) -> RegistryCompany | None:
        number = self._valid(company_number)
        with self._engine.connect() as conn:
            if number:
                rows: Iterable[Any] = conn.execute(
                    text("""SELECT c.company_number, c.legal_form, c.status,
                              (SELECT min(name) FROM register_names n WHERE n.country_code = c.country_code
                                 AND n.company_number = c.company_number) AS name
                            FROM register_companies c WHERE c.country_code = :cc AND c.company_number = :n"""),
                    {"cc": self.country, "n": number},
                ).all()
            else:
                want = self._normalise(company_name)
                if not want:
                    return None
                rows = conn.execute(
                    text("""SELECT DISTINCT c.company_number, c.legal_form, c.status, n.name
                            FROM register_names n JOIN register_companies c USING (country_code, company_number)
                            WHERE n.country_code = :cc AND n.name_norm = :w"""),
                    {"cc": self.country, "w": want},
                ).all()
        by_number = {r.company_number: r for r in rows}
        active = [r for r in by_number.values() if r.status == "active"] or list(by_number.values())
        if len(active) != 1:
            return None
        r = active[0]
        return RegistryCompany(r.company_number, r.name or "", r.legal_form, r.status, self.country)
