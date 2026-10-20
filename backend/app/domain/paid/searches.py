"""Company searches asked for in the operator console ("Find companies") and run by a worker.

An operator asks for a search (activity and area, country, pages). The worker runs it as `pipelineos places discover`
does: Google Places behind the paid-call gate and the monthly cap, then each website must print a register number
the register confirms, and pass the website check. The result (counts and confirmed companies) is stored on the
search for the console to show; the operator then chooses which companies to add to the client's watchlist.

Before go-live (or with FREE_ONLY=true, or without a key) nothing reaches Google: the search ends as "dry_run" (the
would-be request is in the dry-run log) or "failed" with the reason, never silently.
"""

from __future__ import annotations

import uuid
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
from app.domain.paid.discovery import discover_companies
from app.domain.paid.places import GatedPlaces
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
            raise AdapterNotConfigured("COMPANIES_HOUSE_API_KEY is not set (free)")
        return CompaniesHouseRegistry(key.get_secret_value())
    if country == "FR":
        from app.adapters.registries.france import FranceRegistry

        return FranceRegistry()
    raise AdapterNotConfigured(f"no register for country {country!r}")


def request(
    conn: Connection, *, tenant_id: uuid.UUID, operator_id: uuid.UUID, query: str, country: str, pages: int
) -> uuid.UUID:
    search_id = conn.execute(
        text("""INSERT INTO company_searches (tenant_id, query, country, pages, requested_by)
                VALUES (:t, :q, :c, :p, :u) RETURNING id"""),
        {"t": tenant_id, "q": query.strip(), "c": country, "p": pages, "u": operator_id},
    ).scalar_one()
    write_audit(
        conn,
        action="places.search_requested",
        actor_type="user",
        actor_id=str(operator_id),
        tenant_id=tenant_id,
        entity_type="company_search",
        entity_id=search_id,
        after={"query": query.strip(), "country": country, "pages": pages},
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
    time_limit: float = TIME_LIMIT,
) -> str:
    """Runs one queued search once (a redelivered task finds it no longer queued and stops). Returns its status."""
    import json

    with transaction(DbContext.system(tenant_id), engine) as conn:
        row = conn.execute(
            text("""UPDATE company_searches SET status = 'running' WHERE id = :id AND status = 'queued'
                    RETURNING query, country, pages"""),
            {"id": search_id},
        ).first()
    if row is None:
        return "skipped"
    try:
        if places is None:
            from app.domain.paid.providers import places_for

            places = places_for(settings.model_copy(update={"places_region": row.country}), engine, cipher, tenant_id)
        registry = registry or registry_for(settings, row.country)
        finder = finder or WebsiteFinder(contact=settings.crawler_contact)
        report = discover_companies(
            places, finder, registry, row.query, country=row.country, pages=row.pages, time_limit=time_limit
        )
    except AdapterNotConfigured as exc:
        _finish(engine, search_id, tenant_id, status="failed", error=str(exc)[:500])
        return "failed"
    except Exception as exc:  # recorded on the search so the console shows it; details stay in the worker log
        log.exception("places.search_failed", search_id=str(search_id))
        _finish(engine, search_id, tenant_id, status="failed", error=f"unexpected error ({type(exc).__name__})")
        return "failed"
    status = "done" if places.allowed else "dry_run"
    _finish(engine, search_id, tenant_id, status=status, called=places.allowed, report=json.dumps(asdict(report)))
    return status


def recent(conn: Connection, tenant_id: uuid.UUID, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        text("""SELECT id, query, country, pages, status, called_google, report, error, created_at, finished_at
                FROM company_searches WHERE tenant_id = :t ORDER BY created_at DESC LIMIT :n"""),
        {"t": tenant_id, "n": limit},
    ).mappings()
    return [dict(r) for r in rows]
