"""Find companies (operator console): Google Places searches by activity and area, run in the background.

The operator asks; a worker searches and checks each website against the register; the console lists the results,
and the operator adds the companies they want to the client's watchlist with the targeting form's save (so the usual
version check and validation apply). Paid and gated: before go-live nothing reaches Google (see the `setup` block).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, cipher_dep, require, settings_dep
from app.core.crypto import PiiCipher
from app.core.errors import ProblemError
from app.core.logging import get_logger
from app.core.security import Permission
from app.db.session import DbContext, get_engine, transaction
from app.domain.paid import searches
from app.domain.paid.places import PROVIDER, usage
from app.domain.paid.research import PROVIDER as RESEARCH
from app.llm.factory import backend_name
from app.settings import Settings

router = APIRouter(tags=["discovery"])
Operator = Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))]
log = get_logger(__name__)
STUCK_AFTER = timedelta(minutes=20)  # longer than the worker's hard time limit


class SearchIn(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    country: Literal["GB", "FR"] = "FR"
    pages: int = Field(default=1, ge=1, le=3)
    # smart: no query, the AI writes the searches from the client's targeting (app.domain.paid.intelligence)
    smart: bool = False

    @model_validator(mode="after")
    def _query_unless_smart(self) -> SearchIn:
        if not self.smart and len((self.query or "").strip()) < 3:
            raise ValueError("type an activity and an area (at least 3 characters), or run a smart search")
        return self


class FoundOut(BaseModel):
    name: str
    company_number: str
    domain: str
    address: str | None
    country: str
    fit_score: int | None = None  # smart ranking (advice only)
    fit_reason: str | None = None


class SearchOut(BaseModel):
    id: str
    query: str
    country: str
    pages: int
    status: Literal["queued", "running", "done", "dry_run", "failed", "stuck"]
    called_google: bool | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    counts: dict[str, int] = {}
    stopped_early: bool = False
    found: list[FoundOut] = []
    smart: bool = False
    queries: list[str] = []  # the searches made (written by the AI for a smart search)
    ranking: str = "off"  # "done", "off", or "unavailable: why"


class SetupOut(BaseModel):
    """Why searches would or would not reach Google, shown above the form."""

    free_only: bool
    key_set: bool
    adapter_mode: str
    global_go_live: bool
    calls_this_month: int
    monthly_cap: int
    # web research (Gemini + Google Search)
    research_key_set: bool = False
    research_mode: str = "mock"
    research_calls_this_month: int = 0
    research_monthly_cap: int = 0
    # the AI that plans and ranks smart searches for this client right now: gemini | local | anthropic | simulated
    ai: str = "simulated"
    # the countries this platform serves and searches can be made in (first = the default)
    countries: list[str] = ["FR"]
    ai_key_set: bool = False


class SearchesOut(BaseModel):
    setup: SetupOut
    searches: list[SearchOut]


def _out(row: dict[str, Any], now: datetime) -> SearchOut:
    report = row["report"] or {}
    status = row["status"]
    if status in ("queued", "running") and now - row["created_at"] > STUCK_AFTER:
        status = "stuck"
    counts = {k: v for k, v in report.items() if isinstance(v, int) and not isinstance(v, bool)}
    return SearchOut(
        id=str(row["id"]),
        query=row["query"],
        country=row["country"],
        pages=row["pages"],
        status=status,
        called_google=row["called_google"],
        error=row["error"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        counts=counts,
        stopped_early=bool(report.get("stopped_early")),
        found=[FoundOut(**f) for f in report.get("found", [])],
        smart=bool(row.get("smart")),
        queries=[str(q) for q in report.get("queries", [])],
        ranking=str(report.get("ranking", "off")),
    )


@router.get("/clients/{tenant_id}/company-searches", response_model=SearchesOut)
def list_searches(
    tenant_id: uuid.UUID, user: Operator, settings: Annotated[Settings, Depends(settings_dep)]
) -> SearchesOut:
    engine = get_engine()
    with transaction(user.db_context(tenant_id), engine) as conn:
        rows = searches.recent(conn, tenant_id)
        tenant_live = bool(conn.execute(text("SELECT live_mode FROM tenants WHERE id = :t"), {"t": tenant_id}).scalar())
        golive = bool(conn.execute(text("SELECT golive_complete('fr')")).scalar())
    key = settings.google_places_api_key
    setup = SetupOut(
        free_only=settings.free_only,
        key_set=bool(key and key.get_secret_value()),
        adapter_mode=settings.places_adapter_mode,
        global_go_live=settings.global_go_live,
        calls_this_month=usage(engine, PROVIDER),
        monthly_cap=settings.places_monthly_cap,
        research_key_set=bool(settings.gemini_api_key and settings.gemini_api_key.get_secret_value()),
        research_mode=settings.gemini_search_mode,
        research_calls_this_month=usage(engine, RESEARCH),
        research_monthly_cap=settings.gemini_search_monthly_cap,
        ai=backend_name(settings, tenant_live=tenant_live, golive_complete=golive),
        ai_key_set=bool(settings.gemini_api_key and settings.gemini_api_key.get_secret_value()),
        countries=[c for c in settings.served if c in searches.COUNTRIES],
    )
    now = datetime.now(UTC)
    return SearchesOut(setup=setup, searches=[_out(r, now) for r in rows])


@router.post("/clients/{tenant_id}/company-searches", status_code=202, response_model=SearchOut)
def start_search(
    tenant_id: uuid.UUID, body: SearchIn, user: Operator, settings: Annotated[Settings, Depends(settings_dep)]
) -> SearchOut:
    if body.country not in settings.served:
        raise ProblemError(
            422, "Country not served", f"Searches can be made in: {', '.join(settings.served)} (SERVED_COUNTRIES)."
        )
    engine = get_engine()
    try:
        with transaction(user.db_context(tenant_id), engine) as conn:
            search_id = searches.request(
                conn,
                tenant_id=tenant_id,
                operator_id=user.id,
                query=body.query or "",
                country=body.country,
                pages=body.pages,
                smart=body.smart,
                served=settings.served,
            )
    except IntegrityError as exc:
        raise ProblemError(404, "No such client") from exc
    try:
        searches.dispatch(search_id, tenant_id)
    except Exception as exc:  # the job queue is down: say so instead of leaving the search queued for ever
        log.warning("places.search_dispatch_failed", error=type(exc).__name__)
        with transaction(DbContext.system(tenant_id), engine) as conn:
            conn.execute(
                text("""UPDATE company_searches SET status = 'failed', finished_at = now(),
                        error = 'The job queue could not be reached. Try again in a minute.' WHERE id = :id"""),
                {"id": search_id},
            )
    with transaction(user.db_context(tenant_id), engine) as conn:
        row = next(r for r in searches.recent(conn, tenant_id) if r["id"] == search_id)
    return _out(row, datetime.now(UTC))


class ResearchIn(BaseModel):
    question: str = Field(min_length=5, max_length=500)


class SourceOut(BaseModel):
    title: str
    url: str


class ResearchOut(BaseModel):
    """Shown once to the operator who asked, never stored (Google's terms for grounded results)."""

    called: bool
    reason: str | None = None
    answer: str | None = None
    sources: list[SourceOut] = []
    queries: list[str] = []
    suggestions_html: str = ""


@router.post("/clients/{tenant_id}/web-research", response_model=ResearchOut)
def web_research(
    tenant_id: uuid.UUID,
    body: ResearchIn,
    user: Operator,
    settings: Annotated[Settings, Depends(settings_dep)],
    cipher: Annotated[PiiCipher, Depends(cipher_dep)],
) -> ResearchOut:
    """Ask Gemini, grounded on Google Search, about a company. Paid and gated: before go-live (or with FREE_ONLY,
    or without a key) nothing is called and the reason is returned. Ask about companies, never private people."""
    from app.adapters.base import AdapterNotConfigured
    from app.domain.paid.research import research_for

    engine = get_engine()
    with transaction(user.db_context(tenant_id), engine) as conn:
        if conn.execute(text("SELECT 1 FROM tenants WHERE id = :t"), {"t": tenant_id}).scalar() is None:
            raise ProblemError(404, "No such client")
    try:
        result = research_for(settings, engine, cipher, tenant_id).ask(body.question.strip(), user.id)
    except AdapterNotConfigured as exc:
        return ResearchOut(called=False, reason=str(exc))
    a = result.answer
    if a is None:
        return ResearchOut(called=False, reason=result.reason)
    return ResearchOut(
        called=True,
        answer=a.text,
        sources=[SourceOut(title=s.title, url=s.url) for s in a.sources],
        queries=a.queries,
        suggestions_html=a.suggestions_html,
    )
