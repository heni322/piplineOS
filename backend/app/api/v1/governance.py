"""Compliance log, go-live checklist (operator sign-off only), adequacy expiry status."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.api.deps import CurrentUser, cipher_dep, require, settings_dep
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.core.errors import ProblemError
from app.core.security import Permission, verify_totp
from app.db.session import get_engine, transaction
from app.domain.compliance.repository import enable_country
from app.settings import Settings

router = APIRouter(tags=["governance"])


class DecisionOut(BaseModel):
    id: str
    lead_id: str
    stage: str
    outcome: str
    reason_codes: list[str]
    rules_applied: dict[str, str]
    created_at: datetime


@router.get("/compliance/decisions", response_model=list[DecisionOut])
def decisions(
    lead_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(require(Permission.VIEW_COMPLIANCE_LOG))],
    tenant_id: uuid.UUID | None = None,
) -> list[DecisionOut]:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        rows = (
            conn.execute(
                text("SELECT * FROM compliance_decisions WHERE lead_id = :l ORDER BY created_at DESC LIMIT 200"),
                {"l": lead_id},
            )
            .mappings()
            .all()
        )
    return [
        DecisionOut(
            id=str(r["id"]),
            lead_id=str(r["lead_id"]),
            stage=r["stage"],
            outcome=r["outcome"],
            reason_codes=r["reason_codes"],
            rules_applied=r["rules_applied"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


class ChecklistOut(BaseModel):
    id: str
    key: str
    scope: str
    title: str
    description: str
    signed_off: bool
    signed_off_at: datetime | None


class SignOffIn(BaseModel):
    totp_code: str = Field(min_length=6, max_length=10)  # MFA re-prompt for every sign-off
    evidence_url: str | None = Field(default=None, max_length=1000)


@router.get("/golive", response_model=list[ChecklistOut])
def checklist(user: Annotated[CurrentUser, Depends(require(Permission.SIGN_OFF_GOLIVE))]) -> list[ChecklistOut]:
    with transaction(user.db_context(), get_engine()) as conn:
        rows = conn.execute(text("SELECT * FROM golive_checklist_items ORDER BY scope, key")).mappings().all()
    return [
        ChecklistOut(
            id=str(r["id"]),
            key=r["key"],
            scope=r["scope"],
            title=r["title"],
            description=r["description"],
            signed_off=r["signed_off_at"] is not None,
            signed_off_at=r["signed_off_at"],
        )
        for r in rows
    ]


@router.post("/golive/{item_id}/sign-off", status_code=204)
def sign_off(
    item_id: uuid.UUID,
    body: SignOffIn,
    user: Annotated[CurrentUser, Depends(require(Permission.SIGN_OFF_GOLIVE))],
    cipher: Annotated[PiiCipher, Depends(cipher_dep)],
) -> None:
    with transaction(user.db_context(), get_engine()) as conn:
        enc = conn.execute(text("SELECT mfa_secret_enc FROM users WHERE id = :id"), {"id": user.id}).scalar()
        if not enc or not verify_totp(cipher.decrypt(conn, None, "users.mfa_secret", enc), body.totp_code):
            raise ProblemError(403, "MFA verification failed")
        conn.execute(text("SELECT golive_sign_off(:i, :e)"), {"i": item_id, "e": body.evidence_url})


class AdequacyOut(BaseModel):
    key: str
    valid_until: date | None
    days_remaining: int | None
    warning: bool


def adequacy_status(today: date, valid_until: date | None) -> tuple[int | None, bool]:
    if valid_until is None:
        return None, True
    remaining = (valid_until - today).days
    return remaining, remaining <= 365


@router.get("/governance/adequacy", response_model=list[AdequacyOut])
def adequacy(user: Annotated[CurrentUser, Depends(require(Permission.VIEW_ALL_TENANTS))]) -> list[AdequacyOut]:
    with transaction(user.db_context(), get_engine()) as conn:
        rows = (
            conn.execute(text("SELECT key, valid_until FROM current_transfer_mechanisms WHERE kind = 'eu_uk_adequacy'"))
            .mappings()
            .all()
        )
    today = datetime.now(UTC).date()
    out = []
    for r in rows:
        remaining, warn = adequacy_status(today, r["valid_until"])
        out.append(AdequacyOut(key=r["key"], valid_until=r["valid_until"], days_remaining=remaining, warning=warn))
    return out


# ------------------------------------------------------------------------------------------- status
class GovernanceStatus(BaseModel):
    global_go_live: bool
    golive_items: int
    golive_signed: int
    golive_complete: bool
    enabled_countries: list[str]
    live_clients: int
    adapter_modes: dict[str, str]


@router.get("/governance/status", response_model=GovernanceStatus)
def governance_status(
    user: Annotated[CurrentUser, Depends(require(Permission.SIGN_OFF_GOLIVE))],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> GovernanceStatus:
    """The three live keys at a glance. Read-only: nothing here can switch anything on."""
    with transaction(user.db_context(), get_engine()) as conn:
        total, signed = conn.execute(
            text("SELECT count(*), count(signed_off_at) FROM golive_checklist_items WHERE scope = 'uk'")
        ).one()
        complete = bool(conn.execute(text("SELECT golive_complete('uk')")).scalar())
        enabled = list(
            conn.execute(text("SELECT country_code FROM current_country_rollout WHERE enabled ORDER BY 1")).scalars()
        )
        live = int(conn.execute(text("SELECT count(*) FROM tenants WHERE live_mode")).scalar() or 0)
    modes = {
        k: str(getattr(settings, f"{k}_adapter_mode"))
        for k in ("email", "calendar", "crm", "enrichment", "verifier", "signals", "companies_house", "llm")
    }
    return GovernanceStatus(
        global_go_live=settings.global_go_live,
        golive_items=int(total),
        golive_signed=int(signed),
        golive_complete=complete,
        enabled_countries=enabled,
        live_clients=live,
        adapter_modes=modes,
    )


class ReadinessItem(BaseModel):
    key: str
    title: str
    state: str
    who: str
    detail: str


@router.get("/governance/readiness", response_model=list[ReadinessItem])
def governance_readiness(
    user: Annotated[CurrentUser, Depends(require(Permission.SIGN_OFF_GOLIVE))],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> list[ReadinessItem]:
    """What is still missing before real sending, checked automatically. Read-only: it signs nothing."""
    from app.domain.golive.readiness import readiness

    with transaction(user.db_context(), get_engine()) as conn:
        return [ReadinessItem(**i) for i in readiness(conn, settings)]


# ------------------------------------------------------------------------------------------- countries
class CountryOut(BaseModel):
    code: str
    name: str
    stage: str
    enabled: bool
    enabled_at: datetime | None
    rule_mode: str | None
    regime: str | None
    verified_by_counsel: bool
    last_reviewed: date | None
    source: str | None
    can_enable: bool
    blocked_by: list[str]
    build: dict[str, Any] | None = None  # what is built and what is still needed (app.domain.golive.countries)


def _enable_blockers(row: dict[str, object]) -> list[str]:
    out = []
    if row["mode"] is None:
        out.append("NO_RULE")
    elif not row["verified_by_counsel"]:
        out.append("RULE_NOT_VERIFIED_BY_COUNSEL")
    if row["stage"] in ("restricted", "unplanned"):
        out.append("COUNTRY_NOT_IN_ROLLOUT")
    return out


_COUNTRIES_SQL = """SELECT r.country_code, r.name, r.stage, r.enabled, r.enabled_at, c.mode, c.regime,
         r.languages, r.eu_representative_required,
         coalesce(c.verified_by_counsel, false) AS verified_by_counsel, c.last_reviewed, c.source
  FROM current_country_rollout r LEFT JOIN current_compliance_rules c ON c.country_code = r.country_code"""


@router.get("/countries", response_model=list[CountryOut])
def countries(
    user: Annotated[CurrentUser, Depends(require(Permission.SIGN_OFF_GOLIVE))],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> list[CountryOut]:
    from dataclasses import asdict

    from app.domain.golive.countries import country_build

    with transaction(user.db_context(), get_engine()) as conn:
        rows = conn.execute(text(_COUNTRIES_SQL + " ORDER BY r.stage, r.country_code")).mappings().all()
        builds = {r["country_code"]: asdict(country_build(conn, settings, dict(r))) for r in rows}
    out = []
    for r in rows:
        blockers = [] if r["enabled"] else _enable_blockers(dict(r))
        out.append(
            CountryOut(
                code=r["country_code"],
                name=r["name"],
                stage=r["stage"],
                enabled=r["enabled"],
                enabled_at=r["enabled_at"],
                rule_mode=r["mode"],
                regime=r["regime"],
                verified_by_counsel=r["verified_by_counsel"],
                last_reviewed=r["last_reviewed"],
                source=r["source"],
                can_enable=not r["enabled"] and not blockers,
                blocked_by=blockers,
                build=builds[r["country_code"]],
            )
        )
    return out


def _operator_mfa(conn: Connection, cipher: PiiCipher, user: CurrentUser, code: str) -> None:
    if user.role != "operator":
        raise ProblemError(403, "Forbidden")
    enc = conn.execute(text("SELECT mfa_secret_enc FROM users WHERE id = :id"), {"id": user.id}).scalar()
    if not enc or not verify_totp(cipher.decrypt(conn, None, "users.mfa_secret", enc), code):
        raise ProblemError(403, "MFA verification failed")


class CounselIn(BaseModel):
    totp_code: str = Field(min_length=6, max_length=10)
    solicitor: str = Field(min_length=3, max_length=200)  # name and firm of the solicitor who reviewed the rule
    reviewed_on: date


@router.post("/countries/{code}/counsel-verification", status_code=201)
def record_counsel_verification(
    code: str,
    body: CounselIn,
    user: Annotated[CurrentUser, Depends(require(Permission.SIGN_OFF_GOLIVE))],
    cipher: Annotated[PiiCipher, Depends(cipher_dep)],
) -> dict[str, str]:
    """The owner records that their solicitor reviewed this country's rule: a new, verified version of the same
    content. Changing the rule in config later creates a fresh unverified version, so the review never carries over."""
    code = code.upper()
    if body.reviewed_on > datetime.now(UTC).date():
        raise ProblemError(422, "The review date can't be in the future")
    with transaction(user.db_context(), get_engine()) as conn:
        _operator_mfa(conn, cipher, user, body.totp_code)
        cur = (
            conn.execute(
                text("SELECT * FROM compliance_rules WHERE country_code = :c ORDER BY version DESC LIMIT 1"),
                {"c": code},
            )
            .mappings()
            .first()
        )
        if cur is None:
            raise ProblemError(404, "No rule for this country")
        if cur["verified_by_counsel"]:
            raise ProblemError(409, "Already verified by counsel")
        rid = conn.execute(
            text("""INSERT INTO compliance_rules (country_code, version, content_hash, mode, allowed_channels,
                subscriber_rules, required_message_elements, regime, notes, source, verified_by_counsel, verified_by,
                verified_at, last_reviewed, effective_from)
              SELECT country_code, version + 1, content_hash, mode, allowed_channels, subscriber_rules,
                required_message_elements, regime, notes, :src, true, :u, now(), :d, now()
              FROM compliance_rules WHERE id = :id RETURNING id"""),
            {
                "id": cur["id"],
                "u": user.id,
                "d": body.reviewed_on,
                "src": f"{cur['source'] or 'config'} | reviewed by {body.solicitor} on {body.reviewed_on.isoformat()}",
            },
        ).scalar_one()
        write_audit(
            conn,
            action="compliance_rule.counsel_verified",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=None,
            entity_type="compliance_rule",
            entity_id=rid,
            after={"country": code, "solicitor": body.solicitor, "reviewed_on": body.reviewed_on.isoformat()},
        )
    return {"id": str(rid), "status": "verified"}


class EnableIn(BaseModel):
    totp_code: str = Field(min_length=6, max_length=10)


@router.post("/countries/{code}/enable", status_code=204)
def enable(
    code: str,
    body: EnableIn,
    user: Annotated[CurrentUser, Depends(require(Permission.SIGN_OFF_GOLIVE))],
    cipher: Annotated[PiiCipher, Depends(cipher_dep)],
) -> None:
    """Enabling lets the compliance engine consider prospects in this country. It never sends anything by itself:
    sending still needs the three live keys."""
    code = code.upper()
    with transaction(user.db_context(), get_engine()) as conn:
        _operator_mfa(conn, cipher, user, body.totp_code)
        row = conn.execute(text(_COUNTRIES_SQL + " WHERE r.country_code = :c"), {"c": code}).mappings().first()
        if row is None:
            raise ProblemError(404, "Unknown country")
        if row["enabled"]:
            raise ProblemError(409, "Already enabled")
        blockers = _enable_blockers(dict(row))
        if blockers:
            raise ProblemError(409, "Country can't be enabled yet", detail=", ".join(blockers))
        enable_country(conn, code, user.id, True)


class DisableIn(BaseModel):
    reason: str = Field(min_length=10, max_length=1000)


@router.post("/countries/{code}/disable", status_code=204)
def disable(
    code: str, body: DisableIn, user: Annotated[CurrentUser, Depends(require(Permission.SIGN_OFF_GOLIVE))]
) -> None:
    """Disabling is the safe direction, so it needs no authenticator code: only a written reason."""
    if user.role != "operator":
        raise ProblemError(403, "Forbidden")
    code = code.upper()
    with transaction(user.db_context(), get_engine()) as conn:
        enabled = conn.execute(
            text("SELECT enabled FROM current_country_rollout WHERE country_code = :c"), {"c": code}
        ).scalar()
        if enabled is None:
            raise ProblemError(404, "Unknown country")
        if not enabled:
            raise ProblemError(409, "Already disabled")
        enable_country(conn, code, user.id, False)
        write_audit(
            conn,
            action="country.disable_reason",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=None,
            entity_type="country",
            entity_id=code,
            after={"reason": body.reason},
        )
