"""Builds a tenant's compliance footer (shared by outreach, AI replies, confirmations and reminders)."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.config_paths import config_root
from app.domain.outbound.footer import FooterTemplate
from app.domain.tenants.config import TenantConfig


def tenant_footer(conn: Connection, tenant_id: uuid.UUID, cfg: TenantConfig, public_base_url: str) -> str:
    t = (
        conn.execute(
            text("SELECT legal_name, company_number, registered_address, slug FROM tenants WHERE id = :t"),
            {"t": tenant_id},
        )
        .mappings()
        .one()
    )
    tpl = FooterTemplate.load(config_root() / "footers" / f"{cfg.language}.yaml")
    return tpl.render(
        {
            "sender_name": cfg.brand_voice.sender_name,
            "sender_title": cfg.brand_voice.sender_title,
            "company_legal_name": t["legal_name"],
            "registration_jurisdiction": cfg.registration_jurisdiction,
            "company_number": t["company_number"] or "",
            "registered_office_address": t["registered_address"] or "",
            "data_source_summary": cfg.data_source_summary,
            "purpose": cfg.outreach_purpose,
            "privacy_url": f"{public_base_url}/privacy/{t['slug']}",
        }
    )
