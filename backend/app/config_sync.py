"""Validate config/*.yaml and write new immutable versions to the database (ADR-0007)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.domain.compliance.engine import Mode


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CountryConfig(_Strict):
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    name: str
    regime: Literal["uk_gdpr", "eu_gdpr", "other"]
    mode: Mode
    allowed_channels: list[Literal["email", "alternative"]]
    subscriber_rules: dict[Literal["corporate", "individual", "unknown"], Mode] = {}
    required_message_elements: list[str] = []
    rollout_stage: Literal["1", "2", "3", "4", "restricted", "unplanned"]
    languages: list[str]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    time_zones: list[str]
    holiday_calendars: list[str]
    eu_representative_required: bool = False
    notes: str
    source: str
    verified_by_counsel: Literal[False] = False
    last_reviewed: date | None = None

    @field_validator("verified_by_counsel", mode="before")
    @classmethod
    def _no_attestation_in_files(cls, v: Any) -> Any:
        if v is True:
            raise ValueError("verified_by_counsel can only be attested by an operator in the dashboard")
        return v


class TaxSettings(_Strict):
    uk_vat_registered: bool
    uk_vat_number: str | None = None
    uk_vat_registration_threshold_minor: int | None
    threshold_currency: str = "GBP"
    warn_at_pct: list[int] = [80, 90]
    source: str
    verified_by_accountant: Literal[False] = False


class TaxRule(_Strict):
    code: str
    applies_when: dict[str, Any]
    rate_bps: int = Field(ge=0, le=10000)
    invoice_wording: str
    source: str
    verified_by_accountant: Literal[False] = False


class SupplierFile(_Strict):
    legal_name: str = Field(min_length=2)
    company_number: str | None = None
    registered_address: str = Field(min_length=2)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    vat_number: str | None = None
    billing_email: str
    invoice_prefix: str = Field(pattern=r"^[A-Z0-9-]{2,12}$")
    payment_details: str | None = None

    @property
    def placeholder(self) -> bool:
        values = [
            self.legal_name,
            self.company_number,
            self.registered_address,
            self.payment_details,
            self.billing_email,
        ]
        return any(v is None or "TODO" in v or v.endswith(".invalid") for v in values)


class TaxFile(_Strict):
    settings: TaxSettings
    rules: list[TaxRule]


class Mechanism(_Strict):
    key: str
    kind: Literal["uk_adequacy", "eu_uk_adequacy", "uk_idta", "uk_addendum", "eu_scc", "none"]
    description: str
    counterparty: str | None = None
    valid_until: date | None = None
    verified_by_counsel: Literal[False] = False


class SubProcessor(_Strict):
    key: str
    name: str
    purpose: str
    data_categories: list[str]
    location: str
    transfer_mechanism_key: str | None = None
    dpa_url: str | None = None
    status: Literal["proposed", "active", "retired"] = "proposed"


class TransfersFile(_Strict):
    mechanisms: list[Mechanism]
    sub_processors: list[SubProcessor]


class ChecklistItem(_Strict):
    key: str
    scope: str
    title: str
    description: str


class ChecklistFile(_Strict):
    items: list[ChecklistItem]


def content_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def _latest(conn: Connection, table: str, key_col: str | None, key: str | None) -> Any:
    # table/key_col are internal constants, never user input
    where = f"WHERE {key_col} = :k" if key_col else ""
    sql = f"SELECT * FROM {table} {where} ORDER BY version DESC LIMIT 1"  # noqa: S608
    return conn.execute(text(sql), {"k": key}).mappings().first()


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def sync_countries(conn: Connection, root: Path, report: SyncReport) -> None:
    for f in sorted((root / "countries").glob("*.yaml")):
        cfg = CountryConfig.model_validate(_load(f))
        if f.stem != cfg.country_code:
            raise ValueError(f"{f.name}: file name must match country_code")
        rule_part = cfg.model_dump(
            include={
                "mode",
                "allowed_channels",
                "subscriber_rules",
                "required_message_elements",
                "regime",
                "notes",
                "source",
                "last_reviewed",
            },
            mode="json",
        )
        h = content_hash(rule_part)
        cur = _latest(conn, "compliance_rules", "country_code", cfg.country_code)
        if cur and cur["content_hash"] == h:
            report.unchanged.append(f"compliance_rules:{cfg.country_code}")
        else:
            conn.execute(
                text("""
                INSERT INTO compliance_rules (country_code, version, content_hash, mode, allowed_channels,
                  subscriber_rules, required_message_elements, regime, notes, source, last_reviewed)
                VALUES (:c, :v, :h, :m, :ch, CAST(:sr AS jsonb), :rme, :rg, :n, :s, :lr)"""),
                {
                    "c": cfg.country_code,
                    "v": (cur["version"] + 1) if cur else 1,
                    "h": h,
                    "m": cfg.mode.value,
                    "ch": list(cfg.allowed_channels),
                    "sr": json.dumps({k: v.value for k, v in cfg.subscriber_rules.items()}),
                    "rme": cfg.required_message_elements,
                    "rg": cfg.regime,
                    "n": cfg.notes,
                    "s": cfg.source,
                    "lr": cfg.last_reviewed,
                },
            )
            report.created.append(f"compliance_rules:{cfg.country_code}")

        roll_part = cfg.model_dump(
            include={
                "name",
                "rollout_stage",
                "languages",
                "currency",
                "time_zones",
                "holiday_calendars",
                "eu_representative_required",
            },
            mode="json",
        )
        h2 = content_hash(roll_part)
        cur2 = _latest(conn, "country_rollout_status", "country_code", cfg.country_code)
        if cur2 and cur2["content_hash"] == h2:
            report.unchanged.append(f"country_rollout_status:{cfg.country_code}")
            continue
        # enablement is carried forward from the previous version: only an operator action changes it
        conn.execute(
            text("""
            INSERT INTO country_rollout_status (country_code, version, content_hash, name, stage, enabled, enabled_by,
              enabled_at, languages, currency, time_zones, holiday_calendars, eu_representative_required)
            VALUES (:c, :v, :h, :n, :st, :en, :eb, :ea, :l, :cur, :tz, :hc, :eu)"""),
            {
                "c": cfg.country_code,
                "v": (cur2["version"] + 1) if cur2 else 1,
                "h": h2,
                "n": cfg.name,
                "st": cfg.rollout_stage,
                "en": bool(cur2 and cur2["enabled"]),
                "eb": cur2["enabled_by"] if cur2 else None,
                "ea": cur2["enabled_at"] if cur2 else None,
                "l": cfg.languages,
                "cur": cfg.currency,
                "tz": cfg.time_zones,
                "hc": cfg.holiday_calendars,
                "eu": cfg.eu_representative_required,
            },
        )
        report.created.append(f"country_rollout_status:{cfg.country_code}")


def sync_tax(conn: Connection, root: Path, report: SyncReport) -> None:
    tf = TaxFile.model_validate(_load(root / "tax_rules.yaml"))
    s = tf.settings.model_dump(mode="json")
    h = content_hash(s)
    cur = _latest(conn, "tax_settings", None, None)
    if cur and cur["content_hash"] == h:
        report.unchanged.append("tax_settings")
    else:
        conn.execute(
            text("""INSERT INTO tax_settings (version, content_hash, uk_vat_registered, uk_vat_number,
              uk_vat_registration_threshold_minor, threshold_currency, warn_at_pct, source)
            VALUES (:v, :h, :r, :n, :t, :c, :w, :s)"""),
            {
                "v": (cur["version"] + 1) if cur else 1,
                "h": h,
                "r": s["uk_vat_registered"],
                "n": s["uk_vat_number"],
                "t": s["uk_vat_registration_threshold_minor"],
                "c": s["threshold_currency"],
                "w": s["warn_at_pct"],
                "s": s["source"],
            },
        )
        report.created.append("tax_settings")
    for rule in tf.rules:
        d = rule.model_dump(mode="json")
        h = content_hash(d)
        cur = _latest(conn, "tax_rules", "code", rule.code)
        if cur and cur["content_hash"] == h:
            report.unchanged.append(f"tax_rules:{rule.code}")
            continue
        conn.execute(
            text("""INSERT INTO tax_rules (code, version, content_hash, applies_when, rate_bps, invoice_wording,
              source) VALUES (:c, :v, :h, CAST(:a AS jsonb), :r, :w, :s)"""),
            {
                "c": rule.code,
                "v": (cur["version"] + 1) if cur else 1,
                "h": h,
                "a": json.dumps(rule.applies_when),
                "r": rule.rate_bps,
                "w": rule.invoice_wording,
                "s": rule.source,
            },
        )
        report.created.append(f"tax_rules:{rule.code}")


def sync_transfers(conn: Connection, root: Path, report: SyncReport) -> None:
    tf = TransfersFile.model_validate(_load(root / "transfers.yaml"))
    for m in tf.mechanisms:
        d = m.model_dump(mode="json")
        h = content_hash(d)
        cur = _latest(conn, "transfer_mechanisms", "key", m.key)
        if cur and cur["content_hash"] == h:
            report.unchanged.append(f"transfer_mechanisms:{m.key}")
            continue
        conn.execute(
            text("""INSERT INTO transfer_mechanisms (key, version, content_hash, kind, description,
              counterparty, valid_until) VALUES (:k, :v, :h, :kind, :d, :cp, :vu)"""),
            {
                "k": m.key,
                "v": (cur["version"] + 1) if cur else 1,
                "h": h,
                "kind": m.kind,
                "d": m.description,
                "cp": m.counterparty,
                "vu": m.valid_until,
            },
        )
        report.created.append(f"transfer_mechanisms:{m.key}")
    for sp in tf.sub_processors:
        d = sp.model_dump(mode="json")
        h = content_hash(d)
        cur = _latest(conn, "sub_processors", "key", sp.key)
        if cur and cur["content_hash"] == h:
            report.unchanged.append(f"sub_processors:{sp.key}")
            continue
        conn.execute(
            text("""INSERT INTO sub_processors (key, version, content_hash, name, purpose, data_categories,
              location, transfer_mechanism_key, dpa_url, status)
              VALUES (:k, :v, :h, :n, :p, :dc, :l, :tm, :du, :s)"""),
            {
                "k": sp.key,
                "v": (cur["version"] + 1) if cur else 1,
                "h": h,
                "n": sp.name,
                "p": sp.purpose,
                "dc": sp.data_categories,
                "l": sp.location,
                "tm": sp.transfer_mechanism_key,
                "du": sp.dpa_url,
                "s": sp.status,
            },
        )
        report.created.append(f"sub_processors:{sp.key}")


def sync_checklist(conn: Connection, root: Path, report: SyncReport) -> None:
    cf = ChecklistFile.model_validate(_load(root / "golive_checklist.yaml"))
    for item in cf.items:
        res = conn.execute(
            text("""INSERT INTO golive_checklist_items (key, scope, title, description)
              VALUES (:k, :s, :t, :d) ON CONFLICT (key) DO NOTHING"""),
            {"k": item.key, "s": item.scope, "t": item.title, "d": item.description},
        )
        (report.created if res.rowcount else report.unchanged).append(f"golive:{item.key}")


def sync_supplier(conn: Connection, root: Path, report: SyncReport) -> None:
    path = root / "supplier.yaml"
    if not path.exists():
        return
    sf = SupplierFile.model_validate(_load(path))
    d = sf.model_dump(mode="json")
    h = content_hash(d)
    cur = conn.execute(
        text("SELECT version, content_hash FROM supplier_profiles ORDER BY version DESC LIMIT 1")
    ).first()
    if cur and cur.content_hash == h:
        report.unchanged.append("supplier")
        return
    conn.execute(
        text("""INSERT INTO supplier_profiles (version, content_hash, legal_name, company_number, registered_address,
          country_code, vat_number, billing_email, invoice_prefix, payment_details, placeholder)
          VALUES (:v, :h, :n, :cn, :a, :c, :vat, :e, :p, :pd, :ph)"""),
        {
            "v": (cur.version + 1) if cur else 1,
            "h": h,
            "n": sf.legal_name,
            "cn": sf.company_number,
            "a": sf.registered_address,
            "c": sf.country_code,
            "vat": sf.vat_number,
            "e": sf.billing_email,
            "p": sf.invoice_prefix,
            "pd": sf.payment_details,
            "ph": sf.placeholder,
        },
    )
    report.created.append("supplier")


def sync_all(conn: Connection, root: Path) -> SyncReport:
    report = SyncReport()
    sync_countries(conn, root, report)
    sync_tax(conn, root, report)
    sync_transfers(conn, root, report)
    sync_checklist(conn, root, report)
    sync_supplier(conn, root, report)
    return report
