"""Seeds the Phase 3 demo: one fictional tenant, one niche (UK IT MSPs), English, mocked providers, dry-run only.

DEV/TEST DATABASES ONLY. It enables GB in the target database via a synthetic operator — a real deployment must
never do this from code (the operator does it in the dashboard after legal review).
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.adapters.companies_house.mock import MockCompaniesHouse
from app.adapters.enrichment.mock import MockEnrichment
from app.adapters.signals.mock import MockSignalSource
from app.adapters.verifier.mock import MockVerifier
from app.agents.trigger_hunter.agent import SourceBinding
from app.config_paths import config_root
from app.config_sync import sync_all
from app.core.crypto import PiiCipher, blind_index
from app.core.security import hash_password, new_totp_secret
from app.db.session import DbContext, transaction
from app.demo import dataset
from app.domain.compliance.repository import enable_country
from app.domain.suppression.service import RecipientHashes, suppress_email
from app.domain.tenants.config import TenantConfig, save_config
from app.settings import Settings
from app.workflows.pipeline import Providers

DEMO_SLUG = "northwind-demo"


@dataclass
class DemoIds:
    tenant_id: uuid.UUID
    campaign_id: uuid.UUID
    operator_id: uuid.UUID
    operator_email: str
    operator_password: str | None
    operator_totp_secret: str | None


def providers() -> Providers:
    sources = []
    for name, records in dataset.SIGNALS.items():
        src = MockSignalSource(records)
        src.name = f"{name}.mock"
        sources.append(SourceBinding(src, query="managed IT service provider UK"))
    return Providers(sources, MockEnrichment(dataset.PEOPLE), MockVerifier(), MockCompaniesHouse(dataset.REGISTRY))


def seed_demo(engine: Engine, settings: Settings, operator_email: str = "operator@pipelineos.example") -> DemoIds:
    if settings.environment in ("staging", "prod"):
        raise RuntimeError("demo seed is for dev/test databases only")
    cipher = PiiCipher(settings.key("pii_master_key"))
    with transaction(DbContext.system(), engine) as conn:
        sync_all(conn, config_root())
    bkey = settings.key("blind_index_key")
    password: str | None = None
    totp: str | None = None
    with transaction(DbContext(), engine) as conn:
        found = conn.execute(
            text("SELECT id FROM auth_find_user(:b)"), {"b": blind_index(bkey, operator_email, None)}
        ).scalar()
    if found:
        op_id = found
    else:
        op_id, password, totp = uuid.uuid4(), secrets.token_urlsafe(12), new_totp_secret()
        with transaction(DbContext(role="operator", user_id=op_id), engine) as conn:
            conn.execute(
                text("""INSERT INTO users (id, email_bidx, email_enc, password_hash, role, mfa_secret_enc, mfa_enabled)
                  VALUES (:id, :b, :e, :p, 'operator', :m, true)"""),
                {
                    "id": op_id,
                    "b": blind_index(bkey, operator_email, None),
                    "e": cipher.encrypt(conn, None, "users.email", operator_email),
                    "p": hash_password(password),
                    "m": cipher.encrypt(conn, None, "users.mfa_secret", totp),
                },
            )
    with transaction(DbContext(role="operator", user_id=op_id), engine) as conn:
        if not conn.execute(text("SELECT enabled FROM current_country_rollout WHERE country_code = 'GB'")).scalar():
            enable_country(conn, "GB", op_id)
        tenant_id = conn.execute(text("SELECT id FROM tenants WHERE slug = :s"), {"s": DEMO_SLUG}).scalar()
        if tenant_id is None:
            tenant_id = conn.execute(
                text("""INSERT INTO tenants (slug, legal_name, company_number, registered_address,
                  status, role, lia_reference, lia_approved_at, dpa_status, dpa_signed_at, dpa_version)
                  VALUES (:s, 'Northwind Demo Services Ltd', '00000000', '1 Example Street, Leeds, LS1 1AA', 'active',
                  'processor_client', 'LIA-DEMO-001', now(), 'signed', now(), 'v1-demo') RETURNING id"""),
                {"s": DEMO_SLUG},
            ).scalar_one()
    with transaction(DbContext.system(tenant_id), engine) as conn:
        stored = conn.execute(text("SELECT config FROM tenant_configs ORDER BY version DESC LIMIT 1")).scalar()
        if stored is None or TenantConfig.model_validate(stored) != dataset.DEMO_CONFIG:
            save_config(conn, tenant_id, dataset.DEMO_CONFIG, None)  # new version; history is kept
        campaign_id = conn.execute(text("SELECT id FROM campaigns WHERE name = 'UK IT MSPs — demo'")).scalar()
        if campaign_id is None:
            campaign_id = conn.execute(
                text("""INSERT INTO campaigns (tenant_id, name, status, target_countries, daily_cap)
                  VALUES (:t, 'UK IT MSPs — demo', 'active', '{GB}', 50) RETURNING id"""),
                {"t": tenant_id},
            ).scalar_one()
            dom = conn.execute(
                text("""INSERT INTO sending_domains (tenant_id, domain, spf_ok, dkim_ok, dmarc_ok, status)
                  VALUES (:t, 'outreach.northwind-demo.example', true, true, true, 'active') RETURNING id"""),
                {"t": tenant_id},
            ).scalar_one()
            conn.execute(
                text("""INSERT INTO inboxes (tenant_id, sending_domain_id, address, display_name, status, daily_cap)
                  VALUES (:t, :d, 'jordan@outreach.northwind-demo.example', 'Jordan Lee', 'active', 30)"""),
                {"t": tenant_id, "d": dom},
            )
        for email in dataset.PRE_SUPPRESSED:
            suppress_email(
                conn,
                RecipientHashes.for_email(settings.key("suppression_hash_key"), email),
                "objection",
                source_ref="demo: objected to another tenant's campaign",
            )
    return DemoIds(tenant_id, campaign_id, op_id, operator_email, password, totp)
