"""Tenant configuration (versioned in tenant_configs, validated here). Niche playbooks are reusable across tenants."""

from __future__ import annotations

import json
import uuid
from typing import Literal

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


class Criterion(_Strict):
    id: str = Field(pattern=r"^[a-z_]{2,30}$")
    question: str  # what must be true, in plain words
    evidence_from: Literal["reply", "profile"] = "reply"
    hint_keywords: list[str] = []  # helps the deterministic fallback; the model may use other evidence
    required: bool = True


class Qualification(_Strict):
    framework: Literal["bant", "meddic", "custom"] = "custom"
    criteria: list[Criterion] = Field(min_length=1)
    min_confidence: float = Field(default=0.7, ge=0, le=1)


class PlaybookEntry(_Strict):
    id: str = Field(pattern=r"^[a-z_]{2,40}$")
    objection: str  # e.g. "We already have a provider"
    match_keywords: list[str] = Field(min_length=1)
    approved_response: str  # tenant-approved talking point the reply must stay within


class Calendar(_Strict):
    status: Literal["ok", "disconnected"] = "ok"
    calendar_ref: str = "primary"
    host_timezone: str = "Europe/London"
    host_country: str = "GB"
    host_subdivision: str | None = "GB-ENG"
    meeting_minutes: int = Field(default=30, ge=15, le=90)
    day_start_hour: int = Field(default=10, ge=7, le=12)
    day_end_hour: int = Field(default=16, ge=13, le=19)
    min_notice_business_days: int = Field(default=1, ge=0, le=5)
    horizon_business_days: int = Field(default=10, ge=3, le=30)
    slots_to_offer: int = Field(default=3, ge=2, le=5)
    offer_valid_days: int = Field(default=5, ge=1, le=14)


class ShowRate(_Strict):
    reminder_offsets_minutes: list[int] = [1440, 60]
    max_reschedules: int = Field(default=3, ge=0, le=5)
    reminders_after_objection: bool = False
    meeting_title: str = "Introductory call"
    agenda: str = "1. Your current service desk setup\n2. Where overflow cover could help\n3. Next steps, if any"


class Pricing(_Strict):
    """Commercial terms the client signs. Amounts in minor units (pence / cents), excluding VAT."""

    currency: Literal["GBP", "EUR"] = "GBP"
    setup_fee_minor: int = Field(default=0, ge=0)
    monthly_retainer_minor: int = Field(default=0, ge=0)
    per_meeting_minor: int = Field(default=0, ge=0)
    payment_terms_days: int = Field(default=14, ge=1, le=90)
    dispute_window_days: int = Field(default=14, ge=1, le=60)


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
    supported_languages: list[str] = ["en"]
    qualification: Qualification | None = None
    objection_playbook: list[PlaybookEntry] = []
    calendar: Calendar = Calendar()
    show_rate: ShowRate = ShowRate()
    ai_disclosure: Literal["always", "on_request"] = "always"
    ai_disclosure_text: str = "This reply was drafted with the help of AI."
    nurture_days: int = Field(default=90, ge=14, le=365)
    pricing: Pricing | None = None


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
