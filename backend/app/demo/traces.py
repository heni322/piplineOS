"""Synthetic dry-run traces required in every phase report (brief: VALIDATION REQUIREMENTS).

All data is fictional (.example domains). Runs entirely in dry-run mode against whatever database the engine
points at — intended for a dev/test database. GB is enabled here by a synthetic operator, which a real
deployment must never do from code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config_sync import sync_all
from app.core.crypto import PiiCipher, blind_index, email_domain
from app.core.security import hash_password
from app.db.session import DbContext, transaction
from app.domain.compliance.gate import run_gate
from app.domain.compliance.repository import enable_country
from app.domain.compliance.subscriber import classify_subscriber
from app.domain.deliverability.hours import next_business_start
from app.domain.leads.state_machine import State, transition
from app.domain.outbound.footer import FooterTemplate
from app.domain.outbound.gateway import OutboundGateway
from app.domain.outbound.messages import create_draft
from app.domain.suppression.service import RecipientHashes, suppress_email
from app.settings import Settings


@dataclass(frozen=True)
class Persona:
    label: str
    company: str
    domain: str
    legal_form: str
    registry_matched: bool
    company_country: str
    location: str
    subdivision: str | None
    name: str
    title: str
    email: str
    pre_suppressed: bool = False
    suppress_after_approval: bool = False


PERSONAS = [
    Persona(
        "1. UK corporate (permitted)",
        "Contoso Managed IT Ltd",
        "contoso-it.example",
        "ltd",
        True,
        "GB",
        "GB",
        "GB-ENG",
        "Alex Morgan",
        "Head of Service Delivery",
        "alex.morgan@contoso-it.example",
    ),
    Persona(
        "2. UK sole trader (blocked)",
        "Sam Patel IT Consulting",
        "sampatel-it.example",
        "sole_trader",
        True,
        "GB",
        "GB",
        "GB-SCT",
        "Sam Patel",
        "Owner",
        "sam@sampatel-it.example",
    ),
    Persona(
        "3. German prospect at UK company (consent_required)",
        "Fabrikam Cloud Ltd",
        "fabrikam-cloud.example",
        "ltd",
        True,
        "GB",
        "DE",
        None,
        "Jonas Weber",
        "IT Director",
        "jonas.weber@fabrikam-cloud.example",
    ),
    Persona(
        "4. Globally suppressed person",
        "Tailspin Networks Ltd",
        "tailspin-net.example",
        "ltd",
        True,
        "GB",
        "GB",
        "GB-NIR",
        "Riley Quinn",
        "Operations Director",
        "riley.quinn@tailspin-net.example",
        pre_suppressed=True,
    ),
    Persona(
        "5. Suppressed AFTER approval (send-time block)",
        "Woodgrove IT plc",
        "woodgrove-it.example",
        "plc",
        True,
        "GB",
        "GB",
        "GB-WLS",
        "Casey Jones",
        "CTO",
        "casey.jones@woodgrove-it.example",
        suppress_after_approval=True,
    ),
]


def run_traces(engine: Engine, settings: Settings, config_root: Path) -> list[dict[str, Any]]:
    cipher = PiiCipher(settings.key("pii_master_key"))
    skey = settings.key("suppression_hash_key")
    footer_tpl = FooterTemplate.load(config_root / "footers" / "en-GB.yaml")
    run = uuid.uuid4().hex[:8]

    with transaction(DbContext.system(), engine) as conn:
        sync_all(conn, config_root)

    # Synthetic operator (MFA flag set so the DB-level checks are representative) enables GB in THIS database only.
    op_id = uuid.uuid4()
    with transaction(DbContext(role="operator", user_id=op_id), engine) as conn:
        conn.execute(
            text("""INSERT INTO users (id, email_bidx, email_enc, password_hash, role, mfa_secret_enc, mfa_enabled)
              VALUES (:id, :b, :e, :p, 'operator', :m, true)"""),
            {
                "id": op_id,
                "b": blind_index(settings.key("blind_index_key"), f"operator-{run}@pipelineos.example", None),
                "e": cipher.encrypt(conn, None, "users.email", f"operator-{run}@pipelineos.example"),
                "p": hash_password("synthetic-operator-password"),
                "m": cipher.encrypt(conn, None, "users.mfa_secret", "X"),
            },
        )
        enabled = conn.execute(text("SELECT enabled FROM current_country_rollout WHERE country_code = 'GB'")).scalar()
        if not enabled:
            enable_country(conn, "GB", op_id)
        tenant_id = conn.execute(
            text("""INSERT INTO tenants (slug, legal_name, company_number, registered_address, status,
              role, lia_reference, lia_approved_at, dpa_status, dpa_signed_at, dpa_version)
              VALUES (:s, 'Northwind Demo Services Ltd', '00000000', '1 Example Street, Leeds, LS1 1AA', 'active',
              'processor_client', 'LIA-DEMO-001', now(), 'signed', now(), 'v1') RETURNING id"""),
            {"s": f"northwind-demo-{run}"},
        ).scalar_one()

    with transaction(DbContext.system(tenant_id), engine) as conn:
        campaign_id = conn.execute(
            text("""INSERT INTO campaigns (tenant_id, name, status, target_countries, daily_cap)
              VALUES (:t, 'UK IT MSPs — demo', 'active', '{GB}', 50) RETURNING id"""),
            {"t": tenant_id},
        ).scalar_one()
        domain_id = conn.execute(
            text("""INSERT INTO sending_domains (tenant_id, domain, spf_ok, dkim_ok, dmarc_ok, status)
              VALUES (:t, :d, true, true, true, 'active') RETURNING id"""),
            {"t": tenant_id, "d": f"outreach-{run}.northwind-demo.example"},
        ).scalar_one()
        inbox_id = conn.execute(
            text("""INSERT INTO inboxes (tenant_id, sending_domain_id, address, display_name, status,
              daily_cap) VALUES (:t, :d, :a, 'Jordan Lee (Northwind Demo)', 'active', 30) RETURNING id"""),
            {"t": tenant_id, "d": domain_id, "a": f"jordan@outreach-{run}.northwind-demo.example"},
        ).scalar_one()

    footer = footer_tpl.render(
        {
            "sender_name": "Jordan Lee",
            "sender_title": "Partnerships",
            "company_legal_name": "Northwind Demo Services Ltd",
            "registration_jurisdiction": "England & Wales",
            "company_number": "00000000",
            "registered_office_address": "1 Example Street, Leeds, LS1 1AA",
            "data_source_summary": "a licensed B2B data provider (synthetic demo)",
            "purpose": "IT service delivery support",
            "privacy_url": f"{settings.public_base_url}/privacy/northwind-demo-{run}",
        }
    )
    gateway = OutboundGateway(
        settings, engine, cipher, clock=lambda: next_business_start(datetime.now(UTC), "GB", None)
    )
    traces: list[dict[str, Any]] = []
    for p in PERSONAS:
        hashes = RecipientHashes.for_email(skey, p.email)
        with transaction(DbContext.system(tenant_id), engine) as conn:
            if p.pre_suppressed:
                suppress_email(conn, hashes, "objection", source_ref="demo:previous objection (another tenant)")
            sub_type, sub_reason = classify_subscriber(p.legal_form, email_domain(p.email), p.registry_matched)
            company_id = conn.execute(
                text("""INSERT INTO companies (tenant_id, name, name_enc, name_is_personal, domain,
                  domain_normalised, country_code, legal_form, legal_form_source)
                  VALUES (:t, :n, :ne, :personal, :d, :d, :c, :lf, 'companies_house.mock') RETURNING id"""),
                {
                    "t": tenant_id,
                    "personal": p.legal_form == "sole_trader",
                    "n": None if p.legal_form == "sole_trader" else p.company,
                    "ne": cipher.encrypt(conn, tenant_id, "companies.name", p.company)
                    if p.legal_form == "sole_trader"
                    else None,
                    "d": p.domain,
                    "c": p.company_country,
                    "lf": p.legal_form,
                },
            ).scalar_one()
            prospect_id = conn.execute(
                text("""INSERT INTO prospects (tenant_id, company_id, name_enc, title, email_enc,
                  email_bidx, country_code, subdivision_code, subscriber_type, subscriber_type_reason,
                  email_verification, email_verified_at, role_relevance, confidence_score)
                  VALUES (:t, :c, :n, :ti, :e, :b, :cc, :sd, :st, :sr, 'valid', now(), 0.90, 0.85) RETURNING id"""),
                {
                    "t": tenant_id,
                    "c": company_id,
                    "n": cipher.encrypt(conn, tenant_id, "prospects.name", p.name),
                    "ti": p.title,
                    "e": cipher.encrypt(conn, tenant_id, "prospects.email", p.email),
                    "b": blind_index(settings.key("blind_index_key"), p.email, tenant_id),
                    "cc": p.location,
                    "sd": p.subdivision,
                    "st": sub_type,
                    "sr": sub_reason,
                },
            ).scalar_one()
            for f in ("name", "title", "email"):
                conn.execute(
                    text("""INSERT INTO prospect_field_sources (tenant_id, prospect_id, field_name, data_source)
                      VALUES (:t, :p, :f, 'enrichment.mock:demo')"""),
                    {"t": tenant_id, "p": prospect_id, "f": f},
                )
            lead_id = conn.execute(
                text("""INSERT INTO leads (tenant_id, campaign_id, company_id, prospect_id)
                  VALUES (:t, :c, :co, :p) RETURNING id"""),
                {"t": tenant_id, "c": campaign_id, "co": company_id, "p": prospect_id},
            ).scalar_one()
            transition(
                conn,
                lead_id,
                State.ENRICHED,
                actor_type="agent",
                actor_id="decision_maker_finder",
                reason="demo enrichment",
            )
            _, decision = run_gate(conn, tenant_id=tenant_id, lead_id=lead_id, cipher=cipher, suppression_key=skey)

        trace: dict[str, Any] = {
            "lead": p.label,
            "subscriber_type": sub_type,
            "prospect_country": p.location,
            "company_country": p.company_country,
            "queue_decision": decision.outcome,
            "queue_reasons": [r.value for r in decision.reason_codes],
            "rules_applied": dict(decision.rules_applied),
        }
        if decision.outcome == "allow":
            with transaction(DbContext.system(tenant_id), engine) as conn:
                message_id = create_draft(
                    conn,
                    cipher=cipher,
                    suppression_key=skey,
                    tenant_id=tenant_id,
                    lead_id=lead_id,
                    campaign_id=campaign_id,
                    inbox_id=inbox_id,
                    recipient_email=p.email,
                    subject="Service delivery hire at " + p.company.split()[0],
                    body=f"Hi {p.name.split()[0]},\n\nSaw the new Head of Service Delivery role. "
                    "Is reducing ticket backlog a priority this quarter?\n\nJordan",
                    footer=footer,
                    quality_score=8.5,
                    prompt_version="demo",
                )
                transition(conn, lead_id, State.PENDING_APPROVAL, actor_type="agent", actor_id="writer", reason="draft")
                conn.execute(text("UPDATE messages SET status = 'approved' WHERE id = :m"), {"m": message_id})
                conn.execute(
                    text("""INSERT INTO approvals (tenant_id, message_id, reviewer_id, decision, review_seconds)
                      VALUES (:t, :m, :r, 'approved', 22)"""),
                    {"t": tenant_id, "m": message_id, "r": op_id},
                )
                transition(conn, lead_id, State.APPROVED, actor_type="user", actor_id=str(op_id), reason="approved")
                if p.suppress_after_approval:
                    suppress_email(conn, hashes, "unsubscribe", source_ref="demo:unsubscribed via another campaign")
            outcome = gateway.send_message(tenant_id, message_id)
            trace |= {"send_status": outcome.status, "send_reasons": list(outcome.reason_codes), "mode": outcome.mode}
        with transaction(DbContext.system(tenant_id), engine) as conn:
            trace["final_state"] = conn.execute(
                text("SELECT state FROM leads WHERE id = :l"), {"l": lead_id}
            ).scalar_one()
            trace["dry_run_log_rows"] = conn.execute(
                text("SELECT count(*) FROM dry_run_log d JOIN messages m ON m.id = d.entity_id WHERE m.lead_id = :l"),
                {"l": lead_id},
            ).scalar_one()
            trace["alternative_tasks"] = conn.execute(
                text("SELECT count(*) FROM alternative_channel_tasks WHERE lead_id = :l"), {"l": lead_id}
            ).scalar_one()
        traces.append(trace)
    return traces
