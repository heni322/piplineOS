"""Provider selection by adapter mode. A real provider is only used when its adapter mode says so, and a
misconfiguration fails loudly (AdapterNotConfigured) instead of silently calling a real service.

- signals: mock -> the synthetic demo dataset; sandbox/live -> Contracts Finder + Find a Tender award notices
  (free, public, read-only), searched with the client's ICP keywords, plus the client's watchlist: its companies'
  public job boards (Greenhouse, Lever, Ashby; free, no key) and new directors on the Companies House register
- FREE_ONLY=true: no paid provider is ever built. Enrichment -> the Companies House officers list (free; no email
  addresses), verifier -> none ("unknown"), AI -> the local model
- companies house: free, read-only register (needs a free API key); France, Norway, Finland: free open registers;
  Sweden, Denmark: free registers with credentials issued on registration
- enrichment / verifier: Hunter.io, PAID -> built by app.domain.paid behind the three-key gate
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.adapters.base import AdapterNotConfigured
from app.adapters.inbound.base import InboundEmail, Inbox
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


def hiring_query(cfg: TenantConfig) -> str:
    """Hiring keywords for the job boards: "a; b; -excluded" (empty: any new role)."""
    return "; ".join(cfg.icp.hiring_keywords)


def live_sources(cfg: TenantConfig, settings: Settings | None = None) -> list[SourceBinding]:
    """Free sources only: public award notices for everyone; for the client's watchlist, its companies' public job
    boards and (with the free Companies House key) new directors on the register."""
    from app.adapters.signals.job_boards import JobBoardSource, WatchedBoard
    from app.adapters.signals.ocds import contracts_finder, find_a_tender
    from app.agents.trigger_hunter.agent import SourceBinding

    q = signal_query(cfg)
    out = [SourceBinding(contracts_finder(), q), SourceBinding(find_a_tender(), q)]
    boards = [
        WatchedBoard(w.name, w.job_board, w.domain, w.company_number, w.country) for w in cfg.watchlist if w.job_board
    ]
    if boards:
        out.append(SourceBinding(JobBoardSource(boards), hiring_query(cfg)))
    numbered = [w for w in cfg.watchlist if w.company_number]
    key = settings.companies_house_api_key if settings is not None else None
    if numbered and key is not None and key.get_secret_value():
        from app.adapters.companies_house.officers import OfficerAppointmentSource, WatchedCompany

        watched = [WatchedCompany(w.name, w.company_number or "", w.domain) for w in numbered]
        out.append(SourceBinding(OfficerAppointmentSource(watched, key.get_secret_value()), ""))
    return out


def providers_for(settings: Settings, ctx: AgentContext | None = None) -> Providers:
    """Providers for one tenant's run. `ctx` is required as soon as any non-mock signal or paid provider is
    configured, because the queries come from the client's configuration and paid calls need its live flag."""
    from app.demo.seed import providers  # mock providers backed by the synthetic demo dataset

    p = providers()
    needs_ctx = [n for n in ("signals", "enrichment", "verifier") if getattr(settings, f"{n}_adapter_mode") != "mock"]
    if needs_ctx and ctx is None:
        raise AdapterNotConfigured(f"{', '.join(needs_ctx)}: a tenant context is required for real providers")
    if ctx is not None and settings.signals_adapter_mode != "mock":
        p.sources = live_sources(ctx.config, settings)
    if settings.free_only:
        if settings.enrichment_adapter_mode != "mock":  # the free register replaces the paid enrichment provider
            if settings.companies_house_api_key is None or not settings.companies_house_api_key.get_secret_value():
                raise AdapterNotConfigured("FREE_ONLY enrichment needs COMPANIES_HOUSE_API_KEY (free)")
            from app.adapters.companies_house.officers import CompaniesHouseOfficers

            p.enrichment = CompaniesHouseOfficers(settings.companies_house_api_key.get_secret_value())
        if settings.verifier_adapter_mode != "mock":
            from app.adapters.verifier.none import NoVerifier

            p.verifier = NoVerifier()
    elif ctx is not None and (settings.enrichment_adapter_mode != "mock" or settings.verifier_adapter_mode != "mock"):
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
    from app.adapters.companies_house.base import CompaniesRegistry
    from app.adapters.companies_house.mock import MockCompaniesHouse
    from app.adapters.registries.finland import FinlandRegistry
    from app.adapters.registries.france import FranceRegistry
    from app.adapters.registries.norway import NorwayRegistry
    from app.adapters.registries.routing import RoutingRegistry

    # free, read-only public registers (no key); the mock knows no French, Norwegian or Finnish company -> unknown
    real: dict[str, tuple[str, type[FranceRegistry] | type[NorwayRegistry] | type[FinlandRegistry]]] = {
        "FR": (settings.fr_registry_adapter_mode, FranceRegistry),
        "NO": (settings.no_registry_adapter_mode, NorwayRegistry),
        "FI": (settings.fi_registry_adapter_mode, FinlandRegistry),
    }
    by_country: dict[str, CompaniesRegistry] = {"GB": p.registry}
    for cc, (mode, cls) in real.items():
        by_country[cc] = cls() if mode != "mock" else MockCompaniesHouse()
    # free registers that need credentials issued on registration (Sweden: OAuth client; Denmark: user and password)
    by_country["SE"] = MockCompaniesHouse()
    if settings.se_registry_adapter_mode != "mock":
        if not settings.bolagsverket_client_id or settings.bolagsverket_client_secret is None:
            raise AdapterNotConfigured("BOLAGSVERKET_CLIENT_ID / BOLAGSVERKET_CLIENT_SECRET are not set")
        from app.adapters.registries.sweden import SwedenRegistry

        by_country["SE"] = SwedenRegistry(
            settings.bolagsverket_client_id, settings.bolagsverket_client_secret.get_secret_value()
        )
    by_country["DK"] = MockCompaniesHouse()
    if settings.dk_registry_adapter_mode != "mock":
        if not settings.cvr_username or settings.cvr_password is None:
            raise AdapterNotConfigured("CVR_USERNAME / CVR_PASSWORD are not set")
        from app.adapters.registries.denmark import DenmarkRegistry

        by_country["DK"] = DenmarkRegistry(settings.cvr_username, settings.cvr_password.get_secret_value())
    # registers published as open-data files: read the owner-imported local copy (no network at lookup time)
    for cc, mode in (("BE", settings.be_registry_adapter_mode), ("IE", settings.ie_registry_adapter_mode)):
        by_country[cc] = MockCompaniesHouse()
        if mode != "mock":
            from app.adapters.registries.belgium import BelgiumRegistry
            from app.adapters.registries.ireland import IrelandRegistry
            from app.db.session import get_engine

            engine = ctx.engine if ctx is not None else get_engine()
            by_country[cc] = BelgiumRegistry(engine) if cc == "BE" else IrelandRegistry(engine)
    p.registry = RoutingRegistry(by_country)
    return p


def inbox_for(settings: Settings, tenant_id: uuid.UUID) -> Inbox:
    """The tenant's reply mailbox for polling. Real mailboxes push replies (Graph change notifications, Gmail
    Pub/Sub) and are swept every 15 minutes for anything a push missed (app.workflows.inbound_sweep), so there is
    nothing to poll here."""
    if settings.email_adapter_mode != "mock":
        return PushOnlyInbox()
    return MockInbox()


class PushOnlyInbox:
    name = "inbox.push_only"

    def fetch_new(self) -> list[InboundEmail]:
        return []
