"""Builds the paid data providers for a tenant. Only this package may import the Hunter adapter (import contract
`paid-only`). Building an object calls nothing; every call still goes through the gate."""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.adapters.base import AdapterNotConfigured
from app.adapters.hunter import SANDBOX_KEY, HunterEnrichment, HunterVerifier
from app.core.crypto import PiiCipher
from app.db.session import DbContext, transaction
from app.domain.outbound.modes import resolve_mode
from app.domain.paid.gate import GatedEnrichment, GatedVerifier, PaidGate
from app.settings import Settings


def _gate(settings: Settings, engine: Engine, cipher: PiiCipher, tenant_id: uuid.UUID, adapter_mode: str) -> PaidGate:
    with transaction(DbContext.system(tenant_id), engine) as conn:
        live = bool(conn.execute(text("SELECT live_mode FROM tenants WHERE id = :t"), {"t": tenant_id}).scalar())
        golive = bool(conn.execute(text("SELECT golive_complete('uk')")).scalar())
    mode = resolve_mode(
        adapter_mode=adapter_mode, tenant_live=live, global_go_live=settings.global_go_live, golive_complete=golive
    )
    return PaidGate(engine, cipher, settings.key("blind_index_key"), tenant_id, mode)


def _key(settings: Settings, mode: str) -> str:
    if mode == "sandbox":
        return SANDBOX_KEY
    if settings.hunter_api_key is None or not settings.hunter_api_key.get_secret_value():
        raise AdapterNotConfigured("HUNTER_API_KEY is not set")
    return settings.hunter_api_key.get_secret_value()


def enrichment_for(
    settings: Settings,
    engine: Engine,
    cipher: PiiCipher,
    tenant_id: uuid.UUID,
    transport: httpx.BaseTransport | None = None,
) -> GatedEnrichment:
    gate = _gate(settings, engine, cipher, tenant_id, settings.enrichment_adapter_mode)
    return GatedEnrichment(
        HunterEnrichment(_key(settings, settings.enrichment_adapter_mode), transport=transport), gate
    )


def verifier_for(
    settings: Settings,
    engine: Engine,
    cipher: PiiCipher,
    tenant_id: uuid.UUID,
    transport: httpx.BaseTransport | None = None,
) -> GatedVerifier:
    gate = _gate(settings, engine, cipher, tenant_id, settings.verifier_adapter_mode)
    return GatedVerifier(HunterVerifier(_key(settings, settings.verifier_adapter_mode), transport=transport), gate)
