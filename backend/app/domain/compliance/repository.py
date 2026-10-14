"""Loads what the pure engine needs from the database, and persists decisions."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.audit import write_audit
from app.domain.compliance.engine import (
    ComplianceContext,
    ComplianceDecision,
    CountryRule,
    Mode,
    ProspectFacts,
    Stage,
    SuppressionResult,
    TenantGates,
)
from app.domain.compliance.jurisdiction import EEA


def load_rules(conn: Connection) -> dict[str, CountryRule]:
    rows = conn.execute(
        text("""SELECT r.id, r.country_code, r.version, r.mode, r.allowed_channels, r.subscriber_rules,
                       coalesce(c.eu_representative_required, false) AS eu_rep
                FROM current_compliance_rules r LEFT JOIN current_country_rollout c USING (country_code)""")
    ).mappings()
    return {
        r["country_code"]: CountryRule(
            rule_id=str(r["id"]),
            country_code=r["country_code"],
            version=r["version"],
            mode=Mode(r["mode"]),
            allowed_channels=frozenset(r["allowed_channels"]),
            subscriber_rules={k: Mode(v) for k, v in (r["subscriber_rules"] or {}).items()},
            eu_representative_required=bool(r["eu_rep"]),
        )
        for r in rows
    }


def load_enabled_countries(conn: Connection) -> frozenset[str]:
    rows = conn.execute(text("SELECT country_code FROM current_country_rollout WHERE enabled")).scalars()
    return frozenset(rows)


def load_tenant_gates(conn: Connection, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> TenantGates:
    t = (
        conn.execute(
            text("""SELECT role, lia_reference, lia_approved_at, dpa_status, status, country_code, eu_representative
                    FROM tenants WHERE id = :t"""),
            {"t": tenant_id},
        )
        .mappings()
        .one()
    )
    c = conn.execute(text("SELECT status FROM campaigns WHERE id = :c"), {"c": campaign_id}).scalar_one()
    return TenantGates(
        lia_approved=bool(t["lia_reference"] and t["lia_approved_at"]),
        dpa_signed=t["dpa_status"] == "signed",
        is_controller=t["role"] == "controller",
        tenant_status=t["status"],
        campaign_status=c,
        eu_representative=t["country_code"] in EEA or t["eu_representative"] is not None,
    )


def build_context(
    conn: Connection,
    *,
    stage: Stage,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    prospect: ProspectFacts,
    suppression: SuppressionResult,
    channel: str = "email",
) -> ComplianceContext:
    return ComplianceContext(
        stage=stage,
        channel=channel,
        tenant=load_tenant_gates(conn, tenant_id, campaign_id),
        prospect=prospect,
        rules=load_rules(conn),
        enabled_countries=load_enabled_countries(conn),
        suppression=suppression,
    )


def record_decision(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    stage: Stage,
    decision: ComplianceDecision,
    message_id: uuid.UUID | None = None,
) -> uuid.UUID:
    decision_id: uuid.UUID = conn.execute(
        text("""INSERT INTO compliance_decisions (tenant_id, lead_id, message_id, stage, outcome, reason_codes,
                  rules_applied, effective_country_code, suppression_checked_at, engine_version)
                VALUES (:t, :l, :m, :s, :o, :rc, CAST(:ra AS jsonb), :ec, :sc, :ev) RETURNING id"""),
        {
            "t": tenant_id,
            "l": lead_id,
            "m": message_id,
            "s": stage,
            "o": decision.outcome,
            "rc": [r.value for r in decision.reason_codes],
            "ra": json.dumps(dict(decision.rules_applied)),
            "ec": decision.effective_country,
            "sc": decision.suppression_checked_at,
            "ev": decision.engine_version,
        },
    ).scalar_one()
    write_audit(
        conn,
        action=f"compliance.decision.{decision.outcome}",
        actor_type="system",
        actor_id="compliance_engine",
        tenant_id=tenant_id,
        entity_type="lead",
        entity_id=lead_id,
        after={"stage": stage, "reasons": [r.value for r in decision.reason_codes], "decision_id": str(decision_id)},
    )
    return decision_id


def enable_country(conn: Connection, country_code: str, operator_user_id: uuid.UUID, enabled: bool = True) -> int:
    """Operator-only action (caller must hold an operator context): writes a new rollout version."""
    cur = (
        conn.execute(text("SELECT * FROM current_country_rollout WHERE country_code = :c"), {"c": country_code})
        .mappings()
        .one()
    )
    version: int = cur["version"] + 1
    conn.execute(
        text("""
        INSERT INTO country_rollout_status (country_code, version, content_hash, name, stage, enabled, enabled_by,
          enabled_at, languages, currency, time_zones, holiday_calendars, eu_representative_required, notes)
        VALUES (:c, :v, :h, :n, :st, :en, :eb, :ea, :l, :cur, :tz, :hc, :eu, :notes)"""),
        {
            "c": country_code,
            "v": version,
            "h": cur["content_hash"],
            "n": cur["name"],
            "st": cur["stage"],
            "en": enabled,
            "eb": operator_user_id if enabled else None,
            "ea": datetime.now(UTC) if enabled else None,
            "l": cur["languages"],
            "cur": cur["currency"],
            "tz": cur["time_zones"],
            "hc": cur["holiday_calendars"],
            "eu": cur["eu_representative_required"],
            "notes": cur["notes"],
        },
    )
    write_audit(
        conn,
        action="country.enabled" if enabled else "country.disabled",
        actor_type="user",
        actor_id=str(operator_user_id),
        tenant_id=None,
        entity_type="country",
        entity_id=country_code,
        after={"version": version},
    )
    return version
