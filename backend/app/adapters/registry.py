"""Provider selection by adapter mode. A real provider is only used when its adapter mode says so, and a
misconfiguration fails loudly (AdapterNotConfigured) instead of silently calling a real service.

- signals: mock -> the synthetic demo dataset; sandbox/live -> Contracts Finder + Find a Tender award notices
  (free, public, read-only), searched with the client's ICP keywords
- companies house: free, read-only register (needs a free API key)
- enrichment / verifier: Hunter.io, PAID -> built by app.domain.paid behind the three-key gate
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.adapters.base import AdapterNotConfigured
from app.adapters.inbound.base import Inbox
from app.adapters.inbound.mock import MockInbox
from app.settings import Settings

if TYPE_CHECKING:
    from app.agents.base import AgentContext
    from app.agents.trigger_hunter.agent import SourceBinding
    from app.domain.tenants.config import TenantConfig
    from app.workflows.pipeline import Providers


def signal_query(cfg: TenantConfig) -> str:
    """ICP keywords for the public-notice sources: "a; b; -excluded"."""
    return "; ".join([*cfg.icp.industry_keywords, *(f"-{x}" for x in cfg.icp.exclude_keywords)])


def live_sources(cfg: TenantConfig) -> list[SourceBinding]:
    from app.adapters.signals.ocds import contracts_finder, find_a_tender
    from app.agents.trigger_hunter.agent import SourceBinding

    q = signal_query(cfg)
    return [SourceBinding(contracts_finder(), q), SourceBinding(find_a_tender(), q)]


def providers_for(settings: Settings, ctx: AgentContext | None = None) -> Providers:
    """Providers for one tenant's run. `ctx` is required as soon as any non-mock signal or paid provider is
    configured, because the queries come from the client's configuration and paid calls need its live flag."""
    from app.demo.seed import providers  # mock providers backed by the synthetic demo dataset

    p = providers()
    needs_ctx = [n for n in ("signals", "enrichment", "verifier") if getattr(settings, f"{n}_adapter_mode") != "mock"]
    if needs_ctx and ctx is None:
        raise AdapterNotConfigured(f"{', '.join(needs_ctx)}: a tenant context is required for real providers")
    if ctx is not None and settings.signals_adapter_mode != "mock":
        p.sources = live_sources(ctx.config)
    if ctx is not None and (settings.enrichment_adapter_mode != "mock" or settings.verifier_adapter_mode != "mock"):
        from app.domain.paid.providers import enrichment_for, verifier_for

        if settings.enrichment_adapter_mode != "mock":
            p.enrichment = enrichment_for(settings, ctx.engine, ctx.cipher, ctx.tenant_id)
        if settings.verifier_adapter_mode != "mock":
            p.verifier = verifier_for(settings, ctx.engine, ctx.cipher, ctx.tenant_id)
    if settings.companies_house_adapter_mode != "mock":  # read-only public register; free API key
        if settings.companies_house_api_key is None:
            raise AdapterNotConfigured("COMPANIES_HOUSE_API_KEY is not set")
        from app.adapters.companies_house.live import CompaniesHouseRegistry

        p.registry = CompaniesHouseRegistry(settings.companies_house_api_key.get_secret_value())
    return p


def inbox_for(settings: Settings, tenant_id: uuid.UUID) -> Inbox:
    """The tenant's reply mailbox for polling. Real mailboxes push replies via webhooks (Graph, Gmail)."""
    if settings.email_adapter_mode != "mock":
        raise AdapterNotConfigured("inbound mailbox adapters are not implemented until Phase 6")
    return MockInbox()
