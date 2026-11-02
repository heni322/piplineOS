"""Company searches asked for in the operator console ("Find companies") and run by a worker.

An operator asks for a search (activity and area, country, pages). The worker runs it as `pipelineos places discover`
does: Google Places behind the paid-call gate and the monthly cap, then each website must print a register number
the register confirms, and pass the website check. The result (counts and confirmed companies) is stored on the
search for the console to show; the operator then chooses which companies to add to the client's watchlist.

A SMART search (`smart`) has no query: the AI writes the searches from the client's targeting, and for every search
the AI ranks the confirmed companies by fit with a short reason (`app.domain.paid.intelligence`). The checks that
confirm a company are the same; the AI only plans and ranks.

Before go-live (or with FREE_ONLY=true, or without a key) nothing reaches Google: the search ends as "dry_run" (the
would-be request is in the dry-run log) or "failed" with the reason, never silently. PLACES_ADAPTER_MODE=sandbox makes
real, billed searches while testing.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.adapters.base import AdapterNotConfigured
from app.adapters.companies_house.base import CompaniesRegistry
from app.adapters.website_contacts import WebsiteFinder
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.core.logging import get_logger
from app.db.session import DbContext, transaction
from app.domain.paid.discovery import DiscoveryReport, discover_companies
from app.domain.paid.intelligence import LANGUAGES, plan_queries, rank
from app.domain.paid.places import GatedPlaces
from app.domain.tenants.config import TenantConfig, load_config
from app.llm.backends import ProviderRefused
from app.llm.client import LLMClient, LLMEscalation
from app.settings import Settings

log = get_logger(__name__)
TIME_LIMIT = 600.0  # seconds of website checking per search; what was confirmed by then is kept
COUNTRIES = ("GB", "FR")


def registry_for(settings: Settings, country: str) -> CompaniesRegistry:
    """The register that confirms a printed number: Companies House (free key) or the French Sirene directory."""
    if country == "GB":
        from app.adapters.companies_house.live import CompaniesHouseRegistry

        key = settings.companies_house_api_key
        if key is None or not key.get_secret_value():
            raise AdapterNotConfigured(
                "UK searches check companies on Companies House, which needs COMPANIES_HOUSE_API_KEY (free). "
                "For French companies, choose France: the French register needs no key."
            )
        return CompaniesHouseRegistry(key.get_secret_value())
    if country == "FR":
        from app.adapters.registries.france import FranceRegistry

        return FranceRegistry()
    raise AdapterNotConfigured(f"no register for country {country!r}")


SMART_LABEL = "Smart search (AI)"


def request(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    operator_id: uuid.UUID,
    query: str,
    country: str,
    pages: int,
    smart: bool = False,
    served: tuple[str, ...] | None = None,
) -> uuid.UUID:
    query = SMART_LABEL if smart else query
    if smart:  # a smart search plans from the client's targeting: search the client's own country
        try:
            targets = load_config(conn, tenant_id)[1].icp.countries
        except LookupError:
            targets = []
        usable = [c for c in targets if c in COUNTRIES and (served is None or c in served)]
        if country not in usable and usable:
            country = usable[0]
    search_id = conn.execute(
        text("""INSERT INTO company_searches (tenant_id, query, country, pages, requested_by, smart)
                VALUES (:t, :q, :c, :p, :u, :s) RETURNING id"""),
        {"t": tenant_id, "q": query.strip(), "c": country, "p": pages, "u": operator_id, "s": smart},
    ).scalar_one()
    write_audit(
        conn,
        action="places.search_requested",
        actor_type="user",
        actor_id=str(operator_id),
        tenant_id=tenant_id,
        entity_type="company_search",
        entity_id=search_id,
        after={"query": query.strip(), "country": country, "pages": pages, "smart": smart},
    )
    return uuid.UUID(str(search_id))


def dispatch(search_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    """Queue the search for a worker (the API never waits for the crawl)."""
    from app.workflows.celery_app import company_search_task

    company_search_task.delay(str(search_id), str(tenant_id))


def _finish(engine: Engine, search_id: uuid.UUID, tenant_id: uuid.UUID, **fields: Any) -> None:
    with transaction(DbContext.system(tenant_id), engine) as conn:
        conn.execute(
            text("""UPDATE company_searches SET status = :status, called_google = :called,
                    report = CAST(:report AS jsonb), error = :error, finished_at = now() WHERE id = :id"""),
            {"id": search_id, "called": None, "report": None, "error": None} | fields,
        )
        write_audit(
            conn,
            action="places.search_finished",
            actor_type="system",
            actor_id=None,
            tenant_id=tenant_id,
            entity_type="company_search",
            entity_id=search_id,
            after={"status": fields["status"], "called_google": fields.get("called")},
        )


def llm_for(engine: Engine, settings: Settings, cipher: PiiCipher, tenant_id: uuid.UUID) -> LLMClient:
    """The AI for this client, chosen like the agents' (app.llm.factory): Gemini, the local model or the simulator."""
    from app.llm.factory import build_llm_client

    with transaction(DbContext.system(tenant_id), engine) as conn:
        live = bool(conn.execute(text("SELECT live_mode FROM tenants WHERE id = :t"), {"t": tenant_id}).scalar())
        golive = bool(conn.execute(text("SELECT golive_complete('fr')")).scalar())
    return build_llm_client(settings, engine, cipher, tenant_live=live, golive_complete=golive)


def _config(engine: Engine, tenant_id: uuid.UUID) -> TenantConfig | None:
    with transaction(DbContext.system(tenant_id), engine) as conn:
        try:
            return load_config(conn, tenant_id)[1]
        except LookupError:
            return None


def _merge(total: DiscoveryReport, part: DiscoveryReport) -> None:
    for k in ("searched", "with_website", "no_number_on_site", "not_confirmed_by_register", "site_check_failed"):
        setattr(total, k, getattr(total, k) + getattr(part, k))
    total.stopped_early = total.stopped_early or part.stopped_early
    known = {f.company_number for f in total.found}
    total.found += [f for f in part.found if f.company_number not in known]


def run(
    engine: Engine,
    settings: Settings,
    cipher: PiiCipher,
    search_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    places: GatedPlaces | None = None,
    finder: WebsiteFinder | None = None,
    registry: CompaniesRegistry | None = None,
    llm: LLMClient | None = None,
    time_limit: float = TIME_LIMIT,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Runs one queued search once (a redelivered task finds it no longer queued and stops). Returns its status."""
    import json

    with transaction(DbContext.system(tenant_id), engine) as conn:
        row = conn.execute(
            text("""UPDATE company_searches SET status = 'running' WHERE id = :id AND status = 'queued'
                    RETURNING query, country, pages, smart"""),
            {"id": search_id},
        ).first()
    if row is None:
        return "skipped"
    extra: dict[str, Any] = {"smart": bool(row.smart), "queries": [], "ranking": "off"}
    cfg = _config(engine, tenant_id)
    try:
        if row.smart and cfg is None:
            raise AdapterNotConfigured("a smart search needs the client's targeting: configure the client first")
        if row.smart or cfg is not None:
            try:
                llm = llm or llm_for(engine, settings, cipher, tenant_id)
            except AdapterNotConfigured:
                if row.smart:
                    raise
                llm = None  # a manual search still runs; only the ranking is missing
                extra["ranking"] = "unavailable: the AI is not configured"
        if places is None:
            from app.domain.paid.providers import places_for

            places = places_for(
                settings.model_copy(
                    update={"places_region": row.country, "places_language": LANGUAGES.get(row.country, "en")}
                ),
                engine,
                cipher,
                tenant_id,
            )
        registry = registry or registry_for(settings, row.country)
        finder = finder or WebsiteFinder(contact=settings.crawler_contact)
        if row.smart:
            if cfg is None or llm is None:  # both checked above; kept explicit for the type checker
                raise AdapterNotConfigured("a smart search needs the client's targeting and an AI")
            queries = plan_queries(
                llm, cfg, country=row.country, tenant_id=tenant_id, max_queries=settings.smart_search_max_queries
            )
        else:
            queries = [row.query]
        extra["queries"] = queries
        report = DiscoveryReport()
        deadline = clock() + time_limit
        for q in queries:
            left = deadline - clock()
            if left <= 0:
                report.stopped_early = True
                break
            _merge(
                report,
                discover_companies(
                    places, finder, registry, q, country=row.country, pages=row.pages, time_limit=left, clock=clock
                ),
            )
        if report.found and cfg is not None and llm is not None:
            descriptions = {f.domain: finder.describe(f.domain) for f in report.found}
            report.found = rank(
                llm, cfg, report.found, descriptions, tenant_id=tenant_id, language=LANGUAGES.get(row.country, "en")
            )
            extra["ranking"] = "done"
    except ProviderRefused as exc:  # e.g. an invalid Gemini key: say so plainly, with the provider's own reason
        _finish(engine, search_id, tenant_id, status="failed", error=str(exc)[:500])
        return "failed"
    except LLMEscalation as exc:
        _finish(
            engine, search_id, tenant_id, status="failed", error=f"The AI could not plan the searches ({exc.reason})"
        )
        return "failed"
    except AdapterNotConfigured as exc:
        _finish(engine, search_id, tenant_id, status="failed", error=str(exc)[:500])
        return "failed"
    except Exception as exc:  # recorded on the search so the console shows it; details stay in the worker log
        log.exception("places.search_failed", search_id=str(search_id))
        _finish(engine, search_id, tenant_id, status="failed", error=f"unexpected error ({type(exc).__name__})")
        return "failed"
    status = "done" if places.allowed else "dry_run"
    payload = asdict(report) | extra
    _finish(engine, search_id, tenant_id, status=status, called=places.allowed, report=json.dumps(payload))
    return status


def recent(conn: Connection, tenant_id: uuid.UUID, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        text("""SELECT id, query, country, pages, smart, status, called_google, report, error, created_at,
                finished_at FROM company_searches WHERE tenant_id = :t ORDER BY created_at DESC LIMIT :n"""),
        {"t": tenant_id, "n": limit},
    ).mappings()
    return [dict(r) for r in rows]
