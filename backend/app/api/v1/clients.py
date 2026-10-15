"""Client onboarding (operator), terms sign-off (client, in the portal), and reporting."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.api.deps import CurrentUser, cipher_dep, require, settings_dep
from app.api.v1.control_errors import problem_for
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.core.errors import ProblemError
from app.core.security import Permission
from app.db.session import get_engine, transaction
from app.domain.reporting.metrics import metrics, roi
from app.domain.tenants import accounts, controls, onboarding, setup, signoffs
from app.domain.tenants.config import Roi, TenantConfig, current_version, load_config, save_config
from app.settings import Settings

router = APIRouter(tags=["clients"])
Operator = Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))]
Kind = Literal["qualification_criteria", "pricing"]


class ClientIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    legal_name: str = Field(min_length=2, max_length=200)
    company_number: str | None = Field(default=None, max_length=20)
    registered_address: str | None = Field(default=None, max_length=500)
    country_code: str = Field(default="GB", pattern=r"^[A-Z]{2}$")
    billing_currency: Literal["GBP", "EUR"] = "GBP"
    vat_number: str | None = Field(default=None, max_length=30)


class ItemOut(BaseModel):
    key: str
    done: bool
    detail: str


class ClientOut(BaseModel):
    id: str
    slug: str
    legal_name: str
    status: str
    billing_currency: str
    checklist: list[ItemOut]
    paused_reason: str | None = None
    paused_at: datetime | None = None
    company_number: str | None = None
    registered_address: str | None = None
    own: bool = False  # the operator's own outreach (PipelineOS selling itself), not a client


def _client(conn: Any, tenant_id: uuid.UUID) -> ClientOut:
    t = (
        conn.execute(
            text(
                "SELECT id, slug, legal_name, status, billing_currency, paused_reason, paused_at, company_number, "
                "registered_address, is_operator_own FROM tenants "
                "WHERE id = :t"
            ),
            {"t": tenant_id},
        )
        .mappings()
        .first()
    )
    if t is None:
        raise ProblemError(404, "Client not found")
    items = [ItemOut(key=i.key, done=i.done, detail=i.detail) for i in onboarding.checklist(conn, tenant_id)]
    return ClientOut(
        id=str(t["id"]),
        slug=t["slug"],
        legal_name=t["legal_name"],
        status=t["status"],
        billing_currency=t["billing_currency"],
        checklist=items,
        paused_reason=t["paused_reason"],
        paused_at=t["paused_at"],
        company_number=t["company_number"],
        registered_address=t["registered_address"],
        own=bool(t["is_operator_own"]),
    )


@router.get("/clients", response_model=list[ClientOut])
def list_clients(user: Operator) -> list[ClientOut]:
    with transaction(user.db_context(), get_engine()) as conn:
        ids = (
            conn.execute(
                text("SELECT id FROM tenants WHERE status <> 'offboarded' ORDER BY is_operator_own, created_at")
            )
            .scalars()
            .all()
        )
        return [_client(conn, i) for i in ids]


@router.post("/clients", response_model=ClientOut, status_code=201)
def create_client(body: ClientIn, user: Operator) -> ClientOut:
    try:
        with transaction(user.db_context(), get_engine()) as conn:
            tid = onboarding.create_client(conn, operator_id=user.id, **body.model_dump())
            return _client(conn, tid)
    except IntegrityError as exc:
        raise ProblemError(409, "A client with this slug already exists") from exc


@router.put("/clients/{tenant_id}/config")
def put_config(tenant_id: uuid.UUID, body: dict[str, Any], user: Operator) -> dict[str, int]:
    try:
        with transaction(user.db_context(tenant_id), get_engine()) as conn:
            return {"version": onboarding.set_config(conn, tenant_id=tenant_id, operator_id=user.id, config=body)}
    except ValidationError as exc:
        raise ProblemError(422, "Invalid configuration", str(exc.errors()[:5])) from exc


class SetupOut(BaseModel):
    configured: bool
    templates: dict[str, str]
    niche: str | None
    sender_name: str
    sender_title: str
    value_proposition: str
    outreach_purpose: str
    criteria: list[dict[str, Any]]
    pricing: dict[str, Any]
    currency: str
    subject_variants: int = 1


@router.get("/clients/{tenant_id}/setup", response_model=SetupOut)
def get_setup(tenant_id: uuid.UUID, user: Operator) -> SetupOut:
    """The offer and pricing as configured, or the defaults a new client starts from."""
    from app.config_paths import config_root

    tpls = setup.templates(config_root())
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        currency = conn.execute(text("SELECT billing_currency FROM tenants WHERE id = :t"), {"t": tenant_id}).scalar()
        if currency is None:
            raise ProblemError(404, "Client not found")
        try:
            _, cfg = load_config(conn, tenant_id)
        except LookupError:
            cfg = None
    if cfg is None:
        first = next(iter(tpls.values()), None)
        crit = (first["config"].get("qualification") or {}).get("criteria", []) if first else []
        return SetupOut(
            configured=False,
            templates={k: v["title"] for k, v in tpls.items()},
            niche=next(iter(tpls), None),
            sender_name="",
            sender_title="",
            value_proposition="",
            outreach_purpose="",
            criteria=[
                {k: c.get(k, d) for k, d in (("question", ""), ("required", True), ("evidence_from", "reply"))}
                for c in crit
            ],
            pricing=setup.PricingIn().model_dump(),
            currency=currency,
        )
    return SetupOut(
        configured=True,
        templates={k: v["title"] for k, v in tpls.items()},
        niche=cfg.niche_slug,
        sender_name=cfg.brand_voice.sender_name,
        sender_title=cfg.brand_voice.sender_title,
        value_proposition=cfg.brand_voice.value_proposition,
        outreach_purpose=cfg.outreach_purpose,
        criteria=[
            c.model_dump(include={"question", "required", "evidence_from"})
            for c in (cfg.qualification.criteria if cfg.qualification else [])
        ],
        pricing=(cfg.pricing.model_dump(exclude={"currency"}) if cfg.pricing else setup.PricingIn().model_dump()),
        currency=currency,
        subject_variants=cfg.sequence.subject_variants,
    )


@router.put("/clients/{tenant_id}/setup")
def put_setup(tenant_id: uuid.UUID, body: setup.SetupIn, user: Operator) -> dict[str, int]:
    """Saves a new configuration version. Changed criteria or pricing must be accepted again by the client."""
    from app.config_paths import config_root

    tpls = setup.templates(config_root())
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        currency = conn.execute(text("SELECT billing_currency FROM tenants WHERE id = :t"), {"t": tenant_id}).scalar()
        if currency is None:
            raise ProblemError(404, "Client not found")
        try:
            _, current = load_config(conn, tenant_id)
        except LookupError:
            current = None
        if current is None and body.niche not in tpls:
            raise ProblemError(422, "Choose a starting template")
        try:
            cfg = setup.build(current, tpls.get(body.niche or ""), body, currency)
        except setup.SetupError as exc:
            raise ProblemError(422, "Incomplete setup", exc.code) from exc
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
            raise ProblemError(422, "Invalid setup", problems) from exc
        version = onboarding.set_config(conn, tenant_id=tenant_id, operator_id=user.id, config=cfg.model_dump())
    return {"version": version}


class WatchedIn(BaseModel):
    name: str
    company_number: str | None = None
    domain: str | None = None
    country: str | None = None
    job_board: str | None = None


class TargetingIn(BaseModel):
    """Who to look for and where: the parts of a client's configuration the free sources use."""

    industry_keywords: list[str]
    exclude_keywords: list[str] = []
    hiring_keywords: list[str] = []
    sic_codes: list[str] = []
    countries: list[str] = ["GB"]
    watchlist: list[WatchedIn] = []
    # The configuration version the form was opened on: a save based on an older one is refused (409), so the
    # operator's form can't silently undo the client's own target-account edits, or the other way round.
    config_version: int | None = None


def _clean_watchlist(rows: list[WatchedIn]) -> tuple[list[dict[str, Any]], list[int]]:
    """Trimmed rows without blank names, and for each kept row its 1-based row number in what was submitted."""
    kept, numbers = [], []
    for i, w in enumerate(rows):
        if w.name.strip():
            kept.append(
                {k: (v.strip() if isinstance(v, str) else v) or None for k, v in w.model_dump().items()}
                | {"name": w.name.strip()}
            )
            numbers.append(i + 1)
    return kept, numbers


def _stale(conn: Any, tenant_id: uuid.UUID, version: int | None) -> None:
    if version is not None and version != current_version(conn, tenant_id):
        raise ProblemError(409, "Changed since you opened it", "Reload to see the latest version, then save again.")


class TargetingOut(TargetingIn):
    config_version: int
    available_countries: list[str]


def _clean(items: list[str]) -> list[str]:
    return list(dict.fromkeys(i.strip() for i in items if i.strip()))


@router.get("/clients/{tenant_id}/targeting", response_model=TargetingOut)
def get_targeting(tenant_id: uuid.UUID, user: Operator) -> TargetingOut:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        try:
            _, cfg = load_config(conn, tenant_id)
        except LookupError as exc:
            raise ProblemError(409, "This client has no configuration yet") from exc
        version = conn.execute(
            text("SELECT max(version) FROM tenant_configs WHERE tenant_id = :t"), {"t": tenant_id}
        ).scalar_one()
        countries = conn.execute(text("SELECT DISTINCT country_code FROM compliance_rules ORDER BY 1")).scalars().all()
    return TargetingOut(
        industry_keywords=cfg.icp.industry_keywords,
        exclude_keywords=cfg.icp.exclude_keywords,
        hiring_keywords=cfg.icp.hiring_keywords,
        sic_codes=cfg.icp.sic_codes,
        countries=cfg.icp.countries,
        watchlist=[WatchedIn(**w.model_dump()) for w in cfg.watchlist],
        config_version=int(version),
        available_countries=list(countries),
    )


@router.put("/clients/{tenant_id}/targeting")
def put_targeting(tenant_id: uuid.UUID, body: TargetingIn, user: Operator) -> dict[str, int]:
    """Saves a new configuration version with only the targeting changed. Qualification criteria and pricing are
    untouched, so nothing the client signed needs signing again."""
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        try:
            _, cfg = load_config(conn, tenant_id)
        except LookupError as exc:
            raise ProblemError(409, "This client has no configuration yet") from exc
        _stale(conn, tenant_id, body.config_version)
        known = set(conn.execute(text("SELECT DISTINCT country_code FROM compliance_rules")).scalars())
        countries = [c.upper() for c in _clean(body.countries)]
        unknown = [c for c in countries if c not in known]
        if unknown or not countries:
            raise ProblemError(422, "Unknown country", f"No country rules for: {', '.join(unknown) or 'none chosen'}")
        data = cfg.model_dump()
        data["icp"] |= {
            "industry_keywords": _clean(body.industry_keywords),
            "exclude_keywords": _clean(body.exclude_keywords),
            "hiring_keywords": _clean(body.hiring_keywords),
            "sic_codes": _clean(body.sic_codes),
            "countries": countries,
        }
        data["watchlist"] = _clean_watchlist(body.watchlist)[0]
        try:
            version = onboarding.set_config(conn, tenant_id=tenant_id, operator_id=user.id, config=data)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
            raise ProblemError(422, "Invalid targeting", problems) from exc
    return {"version": version}


class LiaIn(BaseModel):
    reference: str = Field(min_length=3, max_length=100)
    document_url: str | None = Field(default=None, max_length=1000)
    approved_on: date


class DpaIn(BaseModel):
    version: str = Field(min_length=1, max_length=50)
    signed_on: date


def _ts(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


@router.post("/clients/{tenant_id}/lia", status_code=204)
def lia(tenant_id: uuid.UUID, body: LiaIn, user: Operator) -> None:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        onboarding.record_lia(
            conn,
            tenant_id=tenant_id,
            operator_id=user.id,
            reference=body.reference,
            document_url=body.document_url,
            approved_at=_ts(body.approved_on),
        )


@router.post("/clients/{tenant_id}/dpa", status_code=204)
def dpa(tenant_id: uuid.UUID, body: DpaIn, user: Operator) -> None:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        onboarding.record_dpa(
            conn, tenant_id=tenant_id, operator_id=user.id, version=body.version, signed_at=_ts(body.signed_on)
        )


class EuRepresentativeIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    contact_email: str = Field(min_length=5, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    address: str | None = Field(default=None, max_length=500)


@router.post("/clients/{tenant_id}/eu-representative", status_code=204)
def eu_representative(tenant_id: uuid.UUID, body: EuRepresentativeIn, user: Operator) -> None:
    try:
        with transaction(user.db_context(tenant_id), get_engine()) as conn:
            onboarding.record_eu_representative(conn, tenant_id=tenant_id, operator_id=user.id, **body.model_dump())
    except onboarding.OnboardingError as exc:
        raise ProblemError(422, "The representative must be in the EU or EEA", ", ".join(exc.missing)) from exc


class ClientUserIn(BaseModel):
    email: str = Field(min_length=5, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Literal["tenant_admin", "tenant_viewer"] = "tenant_admin"


@router.post("/clients/{tenant_id}/users", status_code=201)
def client_user(
    tenant_id: uuid.UUID,
    body: ClientUserIn,
    user: Operator,
    cipher: Annotated[PiiCipher, Depends(cipher_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> dict[str, str]:
    """Creates the client's login and emails them a single-use link to choose a password (dry-run until live).
    No password is ever generated for, shown to or sent by the operator."""
    try:
        with transaction(user.db_context(tenant_id), get_engine()) as conn:
            _, url = accounts.invite(
                conn, settings, cipher, tenant_id=tenant_id, email=body.email, role=body.role, invited_by=user.id
            )
    except IntegrityError as exc:
        raise ProblemError(409, "That email already has an account") from exc
    out = {"email": body.email, "status": "invited"}
    if settings.environment in ("dev", "test"):  # convenience for demos; never shown in staging/prod
        out["dev_invite_link"] = url
    return out


class RecordTermsIn(BaseModel):
    evidence_ref: str = Field(min_length=3, max_length=500)  # e.g. signed order form reference


@router.post("/clients/{tenant_id}/terms/{kind}/record", status_code=201)
def record_terms(tenant_id: uuid.UUID, kind: Kind, body: RecordTermsIn, user: Operator) -> dict[str, str]:
    """For terms the client signed outside the portal (e.g. a signed order form): the evidence is required."""
    try:
        with transaction(user.db_context(tenant_id), get_engine()) as conn:
            sid = signoffs.sign(
                conn,
                tenant_id=tenant_id,
                kind=kind,
                user_id=user.id,
                method="recorded_by_operator",
                evidence_ref=body.evidence_ref,
            )
    except signoffs.NothingToSign as exc:
        raise ProblemError(409, "Nothing to sign: configure it first") from exc
    return {"id": str(sid)}


@router.post("/clients/{tenant_id}/activate", response_model=ClientOut)
def activate(tenant_id: uuid.UUID, user: Operator) -> ClientOut:
    try:
        with transaction(user.db_context(tenant_id), get_engine()) as conn:
            onboarding.activate(conn, tenant_id=tenant_id, operator_id=user.id)
            return _client(conn, tenant_id)
    except onboarding.OnboardingError as exc:
        raise ProblemError(409, "Onboarding incomplete", ", ".join(exc.missing)) from exc


class OwnAccountIn(BaseModel):
    legal_name: str = Field(min_length=2, max_length=200)
    company_number: str | None = Field(default=None, max_length=20)
    registered_address: str | None = Field(default=None, max_length=500)
    billing_currency: Literal["GBP", "EUR"] = "GBP"


@router.post("/own-account", response_model=ClientOut, status_code=201)
def create_own_account(body: OwnAccountIn, user: Operator) -> ClientOut:
    """PipelineOS selling itself: the operator's own outreach, as CONTROLLER (our own LIA, notice and sending
    domain; no DPA, client login or billing). There can only be one."""
    with transaction(user.db_context(), get_engine()) as conn:
        if conn.execute(text("SELECT 1 FROM tenants WHERE is_operator_own")).first():
            raise ProblemError(409, "Your own account already exists")
        slug = "own-outreach"
        if conn.execute(text("SELECT 1 FROM tenants WHERE slug = :s"), {"s": slug}).first():
            slug = f"own-outreach-{uuid.uuid4().hex[:6]}"  # a client already uses the plain slug
        tid = onboarding.create_client(
            conn,
            operator_id=user.id,
            slug=slug,
            legal_name=body.legal_name.strip(),
            company_number=body.company_number,
            registered_address=body.registered_address,
            country_code="GB",
            billing_currency=body.billing_currency,
            vat_number=None,
        )
        try:
            with conn.begin_nested():  # two clicks at once: the unique index allows one own account only
                conn.execute(
                    text("UPDATE tenants SET is_operator_own = true, role = 'controller' WHERE id = :t"), {"t": tid}
                )
        except IntegrityError as exc:
            raise ProblemError(409, "Your own account already exists") from exc
        return _client(conn, tid)


class DetailsIn(BaseModel):
    legal_name: str = Field(min_length=2, max_length=200)
    company_number: str | None = Field(default=None, max_length=20)
    registered_address: str | None = Field(default=None, max_length=500)
    vat_number: str | None = Field(default=None, max_length=30)

    @field_validator("legal_name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if len(v.strip()) < 2:
            raise ValueError("the legal name is required")
        return v


@router.put("/clients/{tenant_id}/details", response_model=ClientOut)
def put_details(tenant_id: uuid.UUID, body: DetailsIn, user: Operator) -> ClientOut:
    """The client's legal identity, printed in every email footer and on invoices. Audited with before/after.
    Issued invoices keep the details they were issued with (they are snapshots)."""
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        before = (
            conn.execute(
                text("SELECT legal_name, company_number, registered_address, vat_number FROM tenants WHERE id = :t"),
                {"t": tenant_id},
            )
            .mappings()
            .first()
        )
        if before is None:
            raise ProblemError(404, "Client not found")
        # Only the fields sent change (a form that leaves out the VAT number must not erase it).
        after = {
            k: (v.strip() if isinstance(v, str) else v) or None
            for k, v in body.model_dump().items()
            if k in body.model_fields_set
        }
        sets = ", ".join(f"{k} = :{k}" for k in after)  # keys are DetailsIn's own field names only
        conn.execute(text(f"UPDATE tenants SET {sets} WHERE id = :t"), after | {"t": tenant_id})  # noqa: S608
        write_audit(
            conn,
            action="tenant.details_changed",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=tenant_id,
            entity_type="tenant",
            entity_id=tenant_id,
            before={k: before[k] for k in after},
            after=after,
        )
        return _client(conn, tenant_id)


# ------------------------------------------------------------------------------------------- emergency stop
class PauseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/clients/{tenant_id}/pause", response_model=ClientOut)
def pause_client(tenant_id: uuid.UUID, body: PauseIn, user: Operator) -> ClientOut:
    """Emergency stop: holds every queued message and lead in flight. Nothing is cancelled; resume continues."""
    try:
        with transaction(user.db_context(tenant_id), get_engine()) as conn:
            controls.pause_client(conn, tenant_id=tenant_id, operator_id=user.id, reason=body.reason)
            return _client(conn, tenant_id)
    except controls.ControlError as exc:
        raise problem_for(exc) from exc


@router.post("/clients/{tenant_id}/resume", response_model=ClientOut)
def resume_client(tenant_id: uuid.UUID, user: Operator) -> ClientOut:
    try:
        with transaction(user.db_context(tenant_id), get_engine()) as conn:
            controls.resume_client(conn, tenant_id=tenant_id, operator_id=user.id)
            return _client(conn, tenant_id)
    except controls.ControlError as exc:
        raise problem_for(exc) from exc


class AccountOut(BaseModel):
    status: str
    paused_reason: str | None
    paused_at: datetime | None


@router.get("/account", response_model=AccountOut)
def account(user: Annotated[CurrentUser, Depends(require(Permission.VIEW_TENANT))]) -> AccountOut:
    if user.tenant_id is None:
        raise ProblemError(422, "Only for client accounts")
    with transaction(user.db_context(), get_engine()) as conn:
        r = conn.execute(
            text("SELECT status, paused_reason, paused_at FROM tenants WHERE id = :t"), {"t": user.tenant_id}
        ).one()
    return AccountOut(status=r.status, paused_reason=r.paused_reason, paused_at=r.paused_at)


@router.post("/account/pause", response_model=AccountOut)
def client_pause(body: PauseIn, user: Annotated[CurrentUser, Depends(require(Permission.SIGN_TERMS))]) -> AccountOut:
    """The client's own emergency stop (client admin). Only the operator resumes, after talking to the client."""
    if user.role != "tenant_admin" or user.tenant_id is None:
        raise ProblemError(403, "Only the client's admin can pause the account")
    try:
        with transaction(user.db_context(), get_engine()) as conn:
            conn.execute(text("SELECT client_pause(:r)"), {"r": body.reason})
            r = conn.execute(
                text("SELECT status, paused_reason, paused_at FROM tenants WHERE id = :t"), {"t": user.tenant_id}
            ).one()
    except DBAPIError as exc:
        # Only the function's own refusals are answered as such. Anything else (a lock timeout, a lost connection)
        # must NOT read as "already stopped": the client would believe sending stopped when it did not.
        code = getattr(exc.orig, "sqlstate", None)
        if code == "P0002":
            raise ProblemError(409, "The account is not active") from exc
        if code == "42501":
            raise ProblemError(403, "Only an active client admin can stop the account") from exc
        raise
    return AccountOut(status=r.status, paused_reason=r.paused_reason, paused_at=r.paused_at)


# ------------------------------------------------------------------------------------------- client self-service
class WatchlistIn(BaseModel):
    watchlist: list[WatchedIn] = Field(max_length=500)
    config_version: int | None = None  # as for targeting: a save on an older version is refused


class WatchlistOut(BaseModel):
    configured: bool
    watchlist: list[WatchedIn]
    max: int = 500
    config_version: int | None = None


ClientAdmin = Annotated[CurrentUser, Depends(require(Permission.SIGN_TERMS))]


@router.get("/account/watchlist", response_model=WatchlistOut)
def get_watchlist(user: Annotated[CurrentUser, Depends(require(Permission.VIEW_TENANT))]) -> WatchlistOut:
    """The client's own target accounts (companies only: no personal data). The free sources watch them."""
    if user.tenant_id is None:
        raise ProblemError(422, "Only for client accounts")
    with transaction(user.db_context(), get_engine()) as conn:
        try:
            _, cfg = load_config(conn, user.tenant_id)
        except LookupError:
            return WatchlistOut(configured=False, watchlist=[])
        version = current_version(conn, user.tenant_id)
    return WatchlistOut(
        configured=True, watchlist=[WatchedIn(**w.model_dump()) for w in cfg.watchlist], config_version=version
    )


@router.put("/account/watchlist", response_model=WatchlistOut)
def put_watchlist(body: WatchlistIn, user: ClientAdmin) -> WatchlistOut:
    """The client admin's own list of companies to watch. Saved as a new configuration version; nothing else in the
    configuration changes, so the criteria and pricing they accepted stay accepted."""
    if user.tenant_id is None:  # SIGN_TERMS is the client admin's permission
        raise ProblemError(403, "Only the client's admin can change the target accounts")
    with transaction(user.db_context(), get_engine()) as conn:
        try:
            _, cfg = load_config(conn, user.tenant_id)
        except LookupError as exc:
            raise ProblemError(409, "Your account is not configured yet") from exc
        _stale(conn, user.tenant_id, body.config_version)
        cleaned, numbers = _clean_watchlist(body.watchlist)
        try:
            new = TenantConfig.model_validate(cfg.model_dump() | {"watchlist": cleaned})
        except ValidationError as exc:

            def row(e: Any) -> str:  # the row number as the client sees it, blank rows included
                loc = e["loc"]
                return str(numbers[loc[1]]) if len(loc) > 1 and isinstance(loc[1], int) else "?"

            problems = "; ".join(f"row {row(e)}: {e['loc'][-1]}: {e['msg']}" for e in exc.errors()[:5])
            raise ProblemError(422, "Some rows are not valid", problems) from exc
        version = onboarding.set_config(conn, tenant_id=user.tenant_id, operator_id=user.id, config=new.model_dump())
        write_audit(
            conn,
            action="tenant.watchlist_set",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=user.tenant_id,
            entity_type="tenant",
            entity_id=user.tenant_id,
            after={"companies": len(cleaned), "by": "client"},
        )
    return WatchlistOut(configured=True, watchlist=[WatchedIn(**w) for w in cleaned], config_version=version)


class ProgressOut(BaseModel):
    status: str
    done: int
    total: int
    items: list[ItemOut]


@router.get("/account/setup-progress", response_model=ProgressOut)
def setup_progress(user: Annotated[CurrentUser, Depends(require(Permission.VIEW_TENANT))]) -> ProgressOut:
    """Where the client's onboarding stands, the same checklist the operator sees."""
    if user.tenant_id is None:
        raise ProblemError(422, "Only for client accounts")
    with transaction(user.db_context(), get_engine()) as conn:
        status = conn.execute(text("SELECT status FROM tenants WHERE id = :t"), {"t": user.tenant_id}).scalar_one()
        items = [ItemOut(key=i.key, done=i.done, detail=i.detail) for i in onboarding.checklist(conn, user.tenant_id)]
    return ProgressOut(status=status, done=sum(i.done for i in items), total=len(items), items=items)


# ------------------------------------------------------------------------------------------- client terms
class TermsOut(BaseModel):
    kind: str
    content: dict[str, Any] | None
    signed: bool
    signed_at: datetime | None


@router.get("/terms", response_model=list[TermsOut])
def terms(
    user: Annotated[CurrentUser, Depends(require(Permission.VIEW_TENANT))], tenant_id: uuid.UUID | None = None
) -> list[TermsOut]:
    t = tenant_id if user.role == "operator" else user.tenant_id
    if t is None:
        raise ProblemError(422, "tenant_id is required")
    with transaction(user.db_context(t), get_engine()) as conn:
        _, cfg = load_config(conn, t)
        out = []
        for kind in ("qualification_criteria", "pricing"):
            content = signoffs.terms_of(cfg, kind)
            h = signoffs.terms_hash(content) if content else None
            at = max((s for hh, s in signoffs.signed(conn, t, kind) if hh == h), default=None)
            out.append(TermsOut(kind=kind, content=content, signed=at is not None, signed_at=at))
        return out


@router.post("/terms/{kind}/sign", status_code=201)
def sign_terms(kind: Kind, user: Annotated[CurrentUser, Depends(require(Permission.SIGN_TERMS))]) -> dict[str, str]:
    if user.role != "tenant_admin" or user.tenant_id is None:
        raise ProblemError(403, "Only the client's admin can accept terms")
    with transaction(user.db_context(), get_engine()) as conn:
        if signoffs.current_is_signed(conn, user.tenant_id, kind):
            raise ProblemError(409, "Already accepted")
        try:
            sid = signoffs.sign(conn, tenant_id=user.tenant_id, kind=kind, user_id=user.id, method="portal")
        except signoffs.NothingToSign as exc:
            raise ProblemError(409, "Nothing to accept yet") from exc
    return {"id": str(sid)}


# ------------------------------------------------------------------------------------------- reporting
@router.get("/reports/metrics")
def report(
    user: Annotated[CurrentUser, Depends(require(Permission.VIEW_TENANT))],
    tenant_id: uuid.UUID | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    t = tenant_id if user.role == "operator" else user.tenant_id
    if t is None:
        raise ProblemError(422, "tenant_id is required")
    today = datetime.now(UTC).date()
    end = end or today
    start = start or (end - timedelta(days=89))
    if start > end or (end - start).days > 400:
        raise ProblemError(422, "Choose a period of up to 400 days")
    with transaction(user.db_context(t), get_engine()) as conn:
        out = metrics(conn, t, start, end)
        out["roi"] = roi(conn, t, out)
        return out


class RoiIn(BaseModel):
    average_deal_value_minor: int = Field(ge=0, le=10_000_000_000)
    close_rate_pct: int = Field(ge=0, le=100)


@router.put("/account/roi", status_code=204)
def set_roi(body: RoiIn, user: Annotated[CurrentUser, Depends(require(Permission.SIGN_TERMS))]) -> None:
    """The client's own deal value and close rate, for the estimated-return figure in their report. Saved as a new
    configuration version; nothing else in the configuration changes (criteria and pricing stay as signed)."""
    if user.role != "tenant_admin" or user.tenant_id is None:
        raise ProblemError(403, "Only the client's admin can set these figures")
    with transaction(user.db_context(), get_engine()) as conn:
        try:
            _, cfg = load_config(conn, user.tenant_id)
        except LookupError as exc:
            raise ProblemError(409, "Your account is not configured yet") from exc
        new = cfg.model_copy(update={"roi": Roi(**body.model_dump())})
        save_config(conn, user.tenant_id, new, user.id)
        write_audit(
            conn,
            action="tenant.roi_set",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=user.tenant_id,
            entity_type="tenant",
            entity_id=user.tenant_id,
            after=body.model_dump(),
        )
