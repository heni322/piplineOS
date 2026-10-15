"""Client onboarding (operator), terms sign-off (client, in the portal), and reporting."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, cipher_dep, require, settings_dep
from app.core.crypto import PiiCipher
from app.core.errors import ProblemError
from app.core.security import Permission
from app.db.session import get_engine, transaction
from app.domain.reporting.metrics import metrics
from app.domain.tenants import accounts, onboarding, signoffs
from app.domain.tenants.config import load_config
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


def _client(conn: Any, tenant_id: uuid.UUID) -> ClientOut:
    t = (
        conn.execute(
            text("SELECT id, slug, legal_name, status, billing_currency FROM tenants WHERE id = :t"), {"t": tenant_id}
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
    )


@router.get("/clients", response_model=list[ClientOut])
def list_clients(user: Operator) -> list[ClientOut]:
    with transaction(user.db_context(), get_engine()) as conn:
        ids = (
            conn.execute(
                text("SELECT id FROM tenants WHERE status <> 'offboarded' AND NOT is_operator_own ORDER BY created_at")
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
        data["watchlist"] = [
            {k: (v.strip() if isinstance(v, str) else v) or None for k, v in w.model_dump().items()}
            | {"name": w.name.strip()}
            for w in body.watchlist
        ]
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
        return metrics(conn, t, start, end)
