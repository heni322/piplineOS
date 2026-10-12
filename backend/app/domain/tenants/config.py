"""Tenant configuration (versioned in tenant_configs, validated here). Niche playbooks are reusable across tenants."""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Persona(_Strict):
    name: str
    titles: list[str] = Field(min_length=1)  # matched case-insensitively against prospect titles
    seniority_keywords: list[str] = []  # partial match (e.g. "head", "director")


class Icp(_Strict):
    description: str
    industry_keywords: list[str] = Field(min_length=1)
    countries: list[str] = ["GB"]
    exclude_keywords: list[str] = []


class BrandVoice(_Strict):
    sender_name: str
    sender_title: str
    tone: str = "plain, specific, friendly-professional; no hype"
    banned_phrases: list[str] = []
    value_proposition: str


class Sequence(_Strict):
    follow_ups: int = Field(default=2, ge=0, le=3)
    spacing_business_days: int = Field(default=3, ge=2, le=10)


class TenantConfig(_Strict):
    schema_version: int = 1
    niche_slug: str
    icp: Icp
    personas: list[Persona] = Field(min_length=1)
    signal_threshold: int = Field(default=60, ge=0, le=100)
    dedupe_window_days: int = Field(default=30, ge=1, le=365)
    brand_voice: BrandVoice
    sequence: Sequence = Sequence()
    outreach_purpose: str
    data_source_summary: str = "a licensed B2B data provider and public professional sources"
    registration_jurisdiction: str = "England & Wales"
    language: str = "en-GB"
    min_quality_score: float = Field(default=8.0, ge=0, le=10)
    max_rewrites: int = Field(default=2, ge=0, le=2)


def save_config(conn: Connection, tenant_id: uuid.UUID, cfg: TenantConfig, created_by: uuid.UUID | None = None) -> int:
    version = int(
        conn.execute(
            text("SELECT coalesce(max(version), 0) + 1 FROM tenant_configs WHERE tenant_id = :t"), {"t": tenant_id}
        ).scalar_one()
    )
    conn.execute(
        text(
            "INSERT INTO tenant_configs (tenant_id, version, schema_version, config, created_by) "
            "VALUES (:t, :v, :sv, CAST(:c AS jsonb), :u)"
        ),
        {"t": tenant_id, "v": version, "sv": cfg.schema_version, "c": cfg.model_dump_json(), "u": created_by},
    )
    return version


def load_config(conn: Connection, tenant_id: uuid.UUID) -> tuple[uuid.UUID, TenantConfig]:
    row = conn.execute(
        text("SELECT id, config FROM tenant_configs WHERE tenant_id = :t ORDER BY version DESC LIMIT 1"),
        {"t": tenant_id},
    ).one_or_none()
    if row is None:
        raise LookupError(f"tenant {tenant_id} has no configuration")
    cfg = row.config if isinstance(row.config, dict) else json.loads(row.config)
    return row.id, TenantConfig.model_validate(cfg)
