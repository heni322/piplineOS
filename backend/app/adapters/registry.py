"""Provider selection by adapter mode. Until Phase 6 only mock implementations exist: live/sandbox modes raise
AdapterNotConfigured, so a misconfiguration fails loudly instead of silently calling a real service."""

from __future__ import annotations

import uuid

from app.adapters.base import AdapterNotConfigured
from app.adapters.inbound.base import Inbox
from app.adapters.inbound.mock import MockInbox
from app.settings import Settings


def providers_for(settings: Settings):  # type: ignore[no-untyped-def]
    for name in ("signals", "enrichment", "verifier"):
        mode = getattr(settings, f"{name}_adapter_mode")
        if mode != "mock":  # the provider is a commercial choice still open (see PHASE-6-REPORT)
            raise AdapterNotConfigured(f"{name} adapter mode {mode!r}: no provider chosen yet")
    from app.demo.seed import providers  # mock providers backed by the synthetic demo dataset

    p = providers()
    if settings.companies_house_adapter_mode != "mock":  # read-only public register; free API key
        if settings.companies_house_api_key is None:
            raise AdapterNotConfigured("COMPANIES_HOUSE_API_KEY is not set")
        from app.adapters.companies_house.live import CompaniesHouseRegistry

        p.registry = CompaniesHouseRegistry(settings.companies_house_api_key.get_secret_value())
    return p


def inbox_for(settings: Settings, tenant_id: uuid.UUID) -> Inbox:
    """The tenant's reply mailbox. Only the mock exists until the Phase 6 email adapters."""
    if settings.email_adapter_mode != "mock":
        raise AdapterNotConfigured("inbound mailbox adapters are not implemented until Phase 6")
    return MockInbox()
