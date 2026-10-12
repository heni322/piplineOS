"""Synthetic test data builders (fictional names, .example domains)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.crypto import PiiCipher, blind_index
from app.core.security import hash_password
from app.db.session import DbContext, transaction
from app.domain.compliance.repository import enable_country
from app.settings import Settings


def make_operator(
    engine: Engine,
    settings: Settings,
    cipher: PiiCipher,
    *,
    email: str | None = None,
    password: str = "operator-password-123",
    totp_secret: str = "JBSWY3DPEHPK3PXP",
) -> uuid.UUID:
    uid = uuid.uuid4()
    email = email or f"op-{uid.hex[:8]}@pipelineos.example"
    with transaction(DbContext(role="operator", user_id=uid), engine) as conn:
        conn.execute(
            text("""INSERT INTO users (id, email_bidx, email_enc, password_hash, role, mfa_secret_enc, mfa_enabled)
              VALUES (:id, :b, :e, :p, 'operator', :m, true)"""),
            {
                "id": uid,
                "b": blind_index(settings.key("blind_index_key"), email, None),
                "e": cipher.encrypt(conn, None, "users.email", email),
                "p": hash_password(password),
                "m": cipher.encrypt(conn, None, "users.mfa_secret", totp_secret),
            },
        )
    return uid


def enable(engine: Engine, operator_id: uuid.UUID, country: str = "GB") -> None:
    with transaction(DbContext(role="operator", user_id=operator_id), engine) as conn:
        enable_country(conn, country, operator_id)


def make_tenant(
    engine: Engine,
    *,
    slug: str | None = None,
    lia: bool = True,
    dpa: bool = True,
    status: str = "active",
    role: str = "processor_client",
    live_mode: bool = False,
) -> uuid.UUID:
    slug = slug or f"t-{uuid.uuid4().hex[:10]}"
    with transaction(DbContext(role="operator"), engine) as conn:
        return conn.execute(
            text("""INSERT INTO tenants (slug, legal_name, company_number, registered_address, status, role,
              lia_reference, lia_approved_at, dpa_status, dpa_signed_at, live_mode)
              VALUES (:s, :n, '00000000', '1 Example St', :st, :r, :lia, CASE WHEN :has_lia THEN now() END,
                      :dpa, CASE WHEN :has_dpa THEN now() END, :live) RETURNING id"""),
            {
                "s": slug,
                "n": f"{slug} Ltd",
                "st": status,
                "r": role,
                "lia": "LIA-1" if lia else None,
                "has_lia": lia,
                "dpa": "signed" if dpa else "none",
                "has_dpa": dpa,
                "live": live_mode,
            },
        ).scalar_one()  # type: ignore[no-any-return]


def make_tenant_user(
    engine: Engine,
    settings: Settings,
    cipher: PiiCipher,
    tenant_id: uuid.UUID,
    *,
    role: str = "tenant_admin",
    email: str | None = None,
    password: str = "tenant-password-123",
) -> tuple[uuid.UUID, str]:
    uid = uuid.uuid4()
    email = email or f"user-{uid.hex[:8]}@client.example"
    with transaction(DbContext(role="operator"), engine) as conn:
        conn.execute(
            text("""INSERT INTO users (id, tenant_id, email_bidx, email_enc, password_hash, role)
              VALUES (:id, :t, :b, :e, :p, :r)"""),
            {
                "id": uid,
                "t": tenant_id,
                "b": blind_index(settings.key("blind_index_key"), email, None),
                "e": cipher.encrypt(conn, None, "users.email", email),
                "p": hash_password(password),
                "r": role,
            },
        )
    return uid, email


@dataclass
class LeadBundle:
    tenant_id: uuid.UUID
    campaign_id: uuid.UUID
    company_id: uuid.UUID
    prospect_id: uuid.UUID
    lead_id: uuid.UUID
    inbox_id: uuid.UUID
    email: str


def make_lead(
    engine: Engine,
    settings: Settings,
    cipher: PiiCipher,
    tenant_id: uuid.UUID,
    *,
    email: str | None = None,
    prospect_country: str = "GB",
    subdivision: str | None = "GB-ENG",
    company_country: str = "GB",
    subscriber_type: str = "corporate",
    verification: str = "valid",
    role_relevance: float = 0.9,
    state: str = "enriched",
    campaign_status: str = "active",
    inbox_cap: int = 30,
    campaign_cap: int = 50,
) -> LeadBundle:
    email = email or f"p-{uuid.uuid4().hex[:8]}@acme-{uuid.uuid4().hex[:4]}.example"
    with transaction(DbContext.system(tenant_id), engine) as conn:
        campaign_id = conn.execute(
            text("INSERT INTO campaigns (tenant_id, name, status, daily_cap) VALUES (:t, 'c', :s, :cap) RETURNING id"),
            {"t": tenant_id, "s": campaign_status, "cap": campaign_cap},
        ).scalar_one()
        dom = conn.execute(
            text("INSERT INTO sending_domains (tenant_id, domain, status) VALUES (:t, :d, 'active') RETURNING id"),
            {"t": tenant_id, "d": f"send-{uuid.uuid4().hex[:8]}.example"},
        ).scalar_one()
        inbox_id = conn.execute(
            text(
                "INSERT INTO inboxes (tenant_id, sending_domain_id, address, display_name, status, "
                "daily_cap) VALUES (:t, :d, :a, 'Rep', 'active', :cap) RETURNING id"
            ),
            {"t": tenant_id, "d": dom, "a": f"rep-{uuid.uuid4().hex[:8]}@send.example", "cap": inbox_cap},
        ).scalar_one()
        company_id = conn.execute(
            text(
                "INSERT INTO companies (tenant_id, name, country_code, legal_form) "
                "VALUES (:t, 'Acme Ltd', :c, 'ltd') RETURNING id"
            ),
            {"t": tenant_id, "c": company_country},
        ).scalar_one()
        prospect_id = conn.execute(
            text("""INSERT INTO prospects (tenant_id, company_id, name_enc, title, email_enc,
              email_bidx, country_code, subdivision_code, subscriber_type, email_verification, role_relevance)
              VALUES (:t, :c, :n, 'Head of IT', :e, :b, :pc, :sd, :st, :v, :rr) RETURNING id"""),
            {
                "t": tenant_id,
                "c": company_id,
                "n": cipher.encrypt(conn, tenant_id, "prospects.name", "Test Person"),
                "e": cipher.encrypt(conn, tenant_id, "prospects.email", email),
                "b": blind_index(settings.key("blind_index_key"), email, tenant_id),
                "pc": prospect_country,
                "sd": subdivision,
                "st": subscriber_type,
                "v": verification,
                "rr": role_relevance,
            },
        ).scalar_one()
        lead_id = conn.execute(
            text(
                "INSERT INTO leads (tenant_id, campaign_id, company_id, prospect_id, state) "
                "VALUES (:t, :c, :co, :p, :s) RETURNING id"
            ),
            {"t": tenant_id, "c": campaign_id, "co": company_id, "p": prospect_id, "s": state},
        ).scalar_one()
    return LeadBundle(tenant_id, campaign_id, company_id, prospect_id, lead_id, inbox_id, email)


def footer_for(engine: Engine, tenant_id: uuid.UUID, public_base_url: str = "http://localhost:3000") -> str:
    """A footer pointing at the tenant's own privacy page (the gateway verifies this exact link)."""
    with transaction(DbContext.system(tenant_id), engine) as conn:
        slug = conn.execute(text("SELECT slug FROM tenants WHERE id = :t"), {"t": tenant_id}).scalar_one()
    return (
        "--\nRep · Acme Ltd\nRegistered No. 00000000 · 1 Example St\nUnsubscribe: {unsubscribe_url}\n"
        f"Privacy: {public_base_url}/privacy/{slug}"
    )
