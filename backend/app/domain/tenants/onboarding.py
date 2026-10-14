"""Client onboarding: create the client, record the legal basics, collect the client's sign-off of criteria and
pricing, and activate only when every item is done. Nothing here sends anything."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.audit import write_audit
from app.domain.tenants import signoffs
from app.domain.tenants.config import TenantConfig, save_config


class OnboardingError(Exception):
    def __init__(self, code: str, missing: list[str] | None = None) -> None:
        super().__init__(code)
        self.code, self.missing = code, missing or []


@dataclass(frozen=True)
class Item:
    key: str
    done: bool
    detail: str


def create_client(
    conn: Connection,
    *,
    operator_id: uuid.UUID,
    slug: str,
    legal_name: str,
    company_number: str | None,
    registered_address: str | None,
    country_code: str,
    billing_currency: str,
    vat_number: str | None,
) -> uuid.UUID:
    tid: uuid.UUID = conn.execute(
        text("""INSERT INTO tenants (slug, legal_name, company_number, registered_address, country_code,
          billing_currency, vat_number, status) VALUES (:s, :n, :cn, :a, :c, :cur, :v, 'onboarding') RETURNING id"""),
        {
            "s": slug,
            "n": legal_name,
            "cn": company_number,
            "a": registered_address,
            "c": country_code,
            "cur": billing_currency,
            "v": vat_number,
        },
    ).scalar_one()
    write_audit(
        conn,
        action="tenant.created",
        actor_type="user",
        actor_id=str(operator_id),
        tenant_id=tid,
        entity_type="tenant",
        entity_id=tid,
        after={"slug": slug},
    )
    return tid


def set_config(conn: Connection, *, tenant_id: uuid.UUID, operator_id: uuid.UUID, config: dict[str, Any]) -> int:
    """A new config version. Changing criteria or pricing means the client must sign again before billing."""
    cfg = TenantConfig.model_validate(config)
    version = save_config(conn, tenant_id, cfg, operator_id)
    write_audit(
        conn,
        action="tenant.config_saved",
        actor_type="user",
        actor_id=str(operator_id),
        tenant_id=tenant_id,
        entity_type="tenant_config",
        entity_id=None,
        after={"version": version},
    )
    return version


def record_lia(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    operator_id: uuid.UUID,
    reference: str,
    document_url: str | None,
    approved_at: datetime,
) -> None:
    conn.execute(
        text("UPDATE tenants SET lia_reference = :r, lia_document_url = :u, lia_approved_at = :a WHERE id = :t"),
        {"r": reference, "u": document_url, "a": approved_at, "t": tenant_id},
    )
    write_audit(
        conn,
        action="tenant.lia_recorded",
        actor_type="user",
        actor_id=str(operator_id),
        tenant_id=tenant_id,
        entity_type="tenant",
        entity_id=tenant_id,
        after={"reference": reference},
    )


def record_dpa(
    conn: Connection, *, tenant_id: uuid.UUID, operator_id: uuid.UUID, version: str, signed_at: datetime
) -> None:
    conn.execute(
        text("UPDATE tenants SET dpa_status = 'signed', dpa_version = :v, dpa_signed_at = :s WHERE id = :t"),
        {"v": version, "s": signed_at, "t": tenant_id},
    )
    write_audit(
        conn,
        action="tenant.dpa_recorded",
        actor_type="user",
        actor_id=str(operator_id),
        tenant_id=tenant_id,
        entity_type="tenant",
        entity_id=tenant_id,
        after={"version": version},
    )


def record_eu_representative(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    operator_id: uuid.UUID,
    name: str,
    contact_email: str,
    country_code: str,
    address: str | None,
) -> None:
    """GDPR Article 27: the client's representative in the EU/EEA, needed before emailing prospects in a country
    whose rules require one (unless the client is established there). Recorded by an operator, audited."""
    from app.domain.compliance.jurisdiction import EEA

    if country_code not in EEA:
        raise OnboardingError("EU_REPRESENTATIVE_OUTSIDE_EEA", [country_code])
    rep = {
        "name": name,
        "contact_email": contact_email,
        "country_code": country_code,
        "address": address,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    conn.execute(
        text("UPDATE tenants SET eu_representative = CAST(:r AS jsonb) WHERE id = :t"),
        {"r": json.dumps(rep), "t": tenant_id},
    )
    write_audit(
        conn,
        action="tenant.eu_representative_recorded",
        actor_type="user",
        actor_id=str(operator_id),
        tenant_id=tenant_id,
        entity_type="tenant",
        entity_id=tenant_id,
        after={"name": name, "country_code": country_code},
    )


def eu_representative_needed(conn: Connection, tenant_country: str, target_countries: list[str]) -> list[str]:
    """Target countries whose rules require an EU/EEA representative, when the client is not established there."""
    from app.domain.compliance.jurisdiction import EEA

    if tenant_country in EEA or not target_countries:
        return []
    rows = conn.execute(
        text("""SELECT country_code FROM current_country_rollout
                WHERE eu_representative_required AND country_code = ANY(:cc) ORDER BY country_code"""),
        {"cc": list(target_countries)},
    ).scalars()
    return list(rows)


def checklist(conn: Connection, tenant_id: uuid.UUID) -> list[Item]:
    t = conn.execute(text("SELECT * FROM tenants WHERE id = :t"), {"t": tenant_id}).mappings().one()
    has_cfg = conn.execute(text("SELECT 1 FROM tenant_configs WHERE tenant_id = :t"), {"t": tenant_id}).first()
    cfg = None
    if has_cfg:
        from app.domain.tenants.config import load_config

        cfg = load_config(conn, tenant_id)[1]
    domain_ok = conn.execute(
        text("SELECT 1 FROM sending_domains WHERE tenant_id = :t AND spf_ok AND dkim_ok AND dmarc_ok"),
        {"t": tenant_id},
    ).first()
    client_user = conn.execute(
        text("SELECT 1 FROM users WHERE tenant_id = :t AND role = 'tenant_admin'"), {"t": tenant_id}
    ).first()
    pricing_ok = bool(cfg and cfg.pricing and cfg.pricing.currency == t["billing_currency"])
    return [
        Item(
            "company_details",
            bool(t["legal_name"] and t["company_number"] and t["registered_address"]),
            "Legal name, company number and registered address (printed in every email footer)",
        ),
        Item("lia", t["lia_approved_at"] is not None, "Legitimate interests assessment approved"),
        Item("dpa", t["dpa_status"] == "signed", "Data processing agreement signed"),
        Item(
            "config",
            cfg is not None and cfg.qualification is not None,
            "Targeting, brand voice and qualification criteria configured",
        ),
        Item("pricing", pricing_ok, "Pricing configured in the client's billing currency"),
        Item("client_user", client_user is not None, "Client admin account created"),
        Item(
            "criteria_signed",
            signoffs.current_is_signed(conn, tenant_id, "qualification_criteria") if cfg else False,
            "Client signed the current qualification criteria",
        ),
        Item(
            "pricing_signed",
            signoffs.current_is_signed(conn, tenant_id, "pricing") if cfg else False,
            "Client signed the current pricing",
        ),
        Item("sending_domain", domain_ok is not None, "A sending domain with SPF, DKIM and DMARC verified"),
        *(
            [
                Item(
                    "eu_representative",
                    t["eu_representative"] is not None,
                    f"EU/EEA representative recorded (Article 27), needed to email {', '.join(need)}",
                )
            ]
            if (need := eu_representative_needed(conn, t["country_code"], cfg.icp.countries if cfg else []))
            else []
        ),
    ]


def activate(conn: Connection, *, tenant_id: uuid.UUID, operator_id: uuid.UUID) -> None:
    missing = [i.key for i in checklist(conn, tenant_id) if not i.done]
    if missing:
        raise OnboardingError("ONBOARDING_INCOMPLETE", missing)
    conn.execute(text("UPDATE tenants SET status = 'active' WHERE id = :t AND status = 'onboarding'"), {"t": tenant_id})
    write_audit(
        conn,
        action="tenant.activated",
        actor_type="user",
        actor_id=str(operator_id),
        tenant_id=tenant_id,
        entity_type="tenant",
        entity_id=tenant_id,
    )
