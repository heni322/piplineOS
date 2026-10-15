"""Billing API: draft and issue invoices (operator, MFA for issuing/voiding), invoices and disputes for clients,
eligibility preview, VAT-threshold monitor, accountant sign-off of tax rules."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.api.deps import CurrentUser, cipher_dep, require
from app.core.crypto import PiiCipher
from app.core.errors import ProblemError
from app.core.security import Permission, verify_totp
from app.db.session import get_engine, transaction
from app.domain.billing import payments
from app.domain.billing import service as billing
from app.domain.billing.eligibility import eligibility
from app.domain.tenants.config import load_config

router = APIRouter(tags=["billing"])
Manager = Annotated[CurrentUser, Depends(require(Permission.MANAGE_BILLING))]
Viewer = Annotated[CurrentUser, Depends(require(Permission.VIEW_BILLING))]
Cipher = Annotated[PiiCipher, Depends(cipher_dep)]

ERROR_STATUS = {
    "NOT_FOUND": 404,
    "ISSUE_BLOCKED": 409,
    "ALREADY_ISSUED": 409,
    "NOT_A_DRAFT": 409,
    "DISPUTE_WINDOW_CLOSED": 409,
    "ALREADY_DISPUTED": 409,
    "NOT_DISPUTABLE": 409,
    "ALREADY_RESOLVED": 409,
    "ALREADY_VERIFIED": 409,
    "BAD_PERIOD": 422,
}


def _err(exc: billing.BillingError) -> ProblemError:
    return ProblemError(
        ERROR_STATUS.get(exc.code, 409),
        exc.code.replace("_", " ").capitalize(),
        ", ".join(exc.reasons) if exc.reasons else exc.detail or None,
    )


def _mfa(conn: Connection, cipher: PiiCipher, user: CurrentUser, code: str) -> None:
    enc = conn.execute(text("SELECT mfa_secret_enc FROM users WHERE id = :u"), {"u": user.id}).scalar()
    if not enc or not verify_totp(cipher.decrypt(conn, None, "users.mfa_secret", enc), code):
        raise ProblemError(403, "MFA verification failed")


# ------------------------------------------------------------------------------------------- invoices
class LineOut(BaseModel):
    id: str
    kind: str
    description: str
    amount_minor: int
    meeting_id: str | None
    evidence: dict[str, Any]
    voided: bool


class InvoiceOut(BaseModel):
    id: str
    tenant_id: str
    number: str
    status: str
    period_start: date | None
    period_end: date | None
    issue_date: date
    due_date: date
    currency: str
    subtotal_minor: int
    tax_minor: int
    total_minor: int
    tax_wording: str | None
    blocked_reasons: list[str]
    supplier: dict[str, Any]
    customer: dict[str, Any]
    lines: list[LineOut] = []
    disputes_open: int = 0
    paid_minor: int = 0
    due_minor: int = 0
    overdue: bool = False
    payments: list[dict[str, Any]] = []


def _invoice(conn: Connection, r: Any, with_lines: bool) -> InvoiceOut:
    lines = []
    if with_lines:
        lines = [
            LineOut(
                id=str(x.id),
                kind=x.kind,
                description=x.description,
                amount_minor=x.amount_minor,
                meeting_id=str(x.meeting_id) if x.meeting_id else None,
                evidence=x.evidence,
                voided=x.voided_at is not None,
            )
            for x in conn.execute(
                text("""SELECT * FROM invoice_lines WHERE invoice_id = :i ORDER BY created_at,
              array_position(ARRAY['setup','retainer','meeting','credit'], kind), description"""),
                {"i": r["id"]},
            )
        ]
    open_d = conn.execute(
        text("""SELECT count(*) FROM disputes d JOIN invoice_lines l ON l.meeting_id = d.meeting_id
          WHERE l.invoice_id = :i AND d.status = 'open'"""),
        {"i": r["id"]},
    ).scalar_one()
    bal = payments.balance(conn, r["id"])
    return InvoiceOut(
        id=str(r["id"]),
        tenant_id=str(r["tenant_id"]),
        number=r["number"],
        status=r["status"],
        period_start=r["period_start"],
        period_end=r["period_end"],
        issue_date=r["issue_date"],
        due_date=r["due_date"],
        currency=r["currency"],
        subtotal_minor=r["subtotal_minor"],
        tax_minor=r["tax_minor"],
        total_minor=r["total_minor"],
        tax_wording=r["tax_wording"],
        blocked_reasons=list(r["blocked_reasons"]),
        supplier=r["supplier_snapshot"],
        customer=r["customer_snapshot"],
        lines=lines,
        disputes_open=open_d,
        paid_minor=bal.paid_minor,
        due_minor=bal.due_minor if r["status"] in ("issued", "paid") else 0,
        overdue=r["status"] == "issued" and bal.due_minor > 0 and r["due_date"] < datetime.now(UTC).date(),
        payments=[
            {
                "amount_minor": int(p.amount_minor),
                "received_on": p.received_on.isoformat(),
                "method": p.method,
                "reference": p.reference,
            }
            for p in conn.execute(
                text("""SELECT amount_minor, received_on, method, reference FROM invoice_payments
                        WHERE invoice_id = :i ORDER BY created_at"""),
                {"i": r["id"]},
            )
        ]
        if with_lines
        else [],
    )


@router.get("/invoices", response_model=list[InvoiceOut])
def list_invoices(user: Viewer, tenant_id: uuid.UUID | None = None, include_void: bool = False) -> list[InvoiceOut]:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        t = tenant_id if user.role == "operator" else user.tenant_id
        rows = (
            conn.execute(
                text("""SELECT * FROM invoices WHERE (CAST(:t AS uuid) IS NULL OR tenant_id = :t)
              AND (:v OR status <> 'void') AND (status <> 'draft' OR :op) ORDER BY period_start DESC, created_at DESC
              LIMIT 200"""),
                {"t": t, "v": include_void, "op": user.role == "operator"},
            )
            .mappings()
            .all()
        )
        return [_invoice(conn, r, False) for r in rows]


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: uuid.UUID, user: Viewer) -> InvoiceOut:
    with transaction(user.db_context(), get_engine()) as conn:
        r = conn.execute(text("SELECT * FROM invoices WHERE id = :i"), {"i": invoice_id}).mappings().first()
        # Clients see issued invoices only; drafts are the operator's working copy.
        if r is None or (user.role != "operator" and r["status"] == "draft"):
            raise ProblemError(404, "Invoice not found")
        return _invoice(conn, r, True)


class DraftIn(BaseModel):
    tenant_id: uuid.UUID
    period_start: date


@router.post("/invoices/draft")
def draft(body: DraftIn, user: Manager) -> dict[str, Any]:
    try:
        with transaction(user.db_context(body.tenant_id), get_engine()) as conn:
            res = billing.refresh_draft(
                conn,
                tenant_id=body.tenant_id,
                period_start=body.period_start,
                today=datetime.now(UTC).date(),
                actor_id=str(user.id),
            )
    except billing.BillingError as exc:
        raise _err(exc) from exc
    return {
        "invoice_id": str(res.invoice_id) if res.invoice_id else None,
        "lines": res.lines,
        "excluded": res.excluded,
        "blocked_reasons": res.blocked_reasons,
        "total_minor": res.total_minor,
    }


class MfaIn(BaseModel):
    totp_code: str = Field(min_length=6, max_length=10)


class VoidIn(MfaIn):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/invoices/{invoice_id}/issue")
def issue(invoice_id: uuid.UUID, body: MfaIn, user: Manager, cipher: Cipher) -> dict[str, str]:
    with transaction(user.db_context(), get_engine()) as conn:
        _mfa(conn, cipher, user, body.totp_code)
    try:
        with transaction(user.db_context(), get_engine()) as conn:
            number = billing.issue(conn, invoice_id=invoice_id, user_id=user.id, today=datetime.now(UTC).date())
    except billing.BillingError as exc:
        if exc.code == "ISSUE_BLOCKED":  # record why, then refuse
            with transaction(user.db_context(), get_engine()) as conn:
                conn.execute(
                    text("UPDATE invoices SET blocked_reasons = :b WHERE id = :i AND status = 'draft'"),
                    {"b": exc.reasons, "i": invoice_id},
                )
        raise _err(exc) from exc
    return {"status": "issued", "number": number}


@router.post("/invoices/{invoice_id}/void")
def void(invoice_id: uuid.UUID, body: VoidIn, user: Manager, cipher: Cipher) -> dict[str, str]:
    with transaction(user.db_context(), get_engine()) as conn:
        _mfa(conn, cipher, user, body.totp_code)
        st = conn.execute(text("SELECT status FROM invoices WHERE id = :i"), {"i": invoice_id}).scalar()
        if st is None:
            raise ProblemError(404, "Invoice not found")
        if st != "issued":
            raise ProblemError(409, "Only issued, unpaid invoices can be voided")
        if payments.balance(conn, invoice_id).paid_minor != 0:
            raise ProblemError(409, "Money was received against this invoice: record a reversal first")
        billing.void_issued(conn, invoice_id=invoice_id, user_id=user.id, reason=body.reason)
    return {"status": "void"}


class PaymentIn(BaseModel):
    amount_minor: int
    received_on: date
    method: Literal["bank_transfer", "card", "direct_debit", "other", "reversal"] = "bank_transfer"
    reference: str | None = Field(default=None, max_length=100)
    totp_code: str = Field(min_length=6, max_length=8)


PAYMENT_ERRORS = {
    "NOT_FOUND": (404, "Invoice not found"),
    "NOT_ISSUED": (409, "Only issued invoices take payments"),
    "ZERO_AMOUNT": (422, "Enter an amount"),
    "REVERSAL_MUST_BE_NEGATIVE": (422, "A reversal is a negative amount, and only a reversal is"),
    "DATE_OUT_OF_RANGE": (422, "The date must be between the issue date and today"),
    "REVERSAL_EXCEEDS_RECEIVED": (422, "You can't reverse more than was received"),
    "INVOICE_ALREADY_PAID": (409, "This invoice is paid; a refund needs a credit note"),
}


@router.post("/invoices/{invoice_id}/payments", response_model=InvoiceOut)
def record_payment(invoice_id: uuid.UUID, body: PaymentIn, user: Manager, cipher: Cipher) -> InvoiceOut:
    """Money received (e.g. a bank transfer seen on the statement). Needs your authenticator code, like issuing."""
    with transaction(user.db_context(), get_engine()) as conn:
        _mfa(conn, cipher, user, body.totp_code)
    try:
        with transaction(user.db_context(), get_engine()) as conn:
            payments.record_payment(
                conn,
                invoice_id=invoice_id,
                user_id=user.id,
                amount_minor=body.amount_minor,
                received_on=body.received_on,
                method=body.method,
                reference=body.reference,
                today=datetime.now(UTC).date(),
            )
            r = conn.execute(text("SELECT * FROM invoices WHERE id = :i"), {"i": invoice_id}).mappings().one()
            return _invoice(conn, r, True)
    except payments.PaymentError as exc:
        status, title = PAYMENT_ERRORS.get(exc.code, (409, exc.code))
        raise ProblemError(status, title) from exc


# ------------------------------------------------------------------------------------------- eligibility preview
class EligibilityOut(BaseModel):
    meeting_id: str
    company: str
    starts_at: datetime
    status: str
    billable: bool
    reasons: list[str]


@router.get("/billing/meetings", response_model=list[EligibilityOut])
def billable_meetings(user: Viewer, tenant_id: uuid.UUID | None = None) -> list[EligibilityOut]:
    t = tenant_id if user.role == "operator" else user.tenant_id
    if t is None:
        raise ProblemError(422, "tenant_id is required")
    with transaction(user.db_context(t), get_engine()) as conn:
        _, cfg = load_config(conn, t)
        rows = (
            conn.execute(
                text("""SELECT m.*, c.name AS company FROM meetings m JOIN leads l ON l.id = m.lead_id
              JOIN companies c ON c.id = l.company_id WHERE m.tenant_id = :t AND m.status <> 'proposed'
              ORDER BY m.starts_at DESC LIMIT 200"""),
                {"t": t},
            )
            .mappings()
            .all()
        )
        out = []
        facts = billing.meeting_facts_bulk(conn, t, list(rows), cfg.pricing)
        for m in rows:
            ok, reasons = eligibility(facts[m["id"]])
            out.append(
                EligibilityOut(
                    meeting_id=str(m["id"]),
                    company=m["company"] or "Sole trader",
                    starts_at=m["starts_at"],
                    status=m["status"],
                    billable=ok,
                    reasons=reasons,
                )
            )
        return out


# ------------------------------------------------------------------------------------------- disputes
class DisputeIn(BaseModel):
    meeting_id: uuid.UUID
    kind: Literal["attendance", "qualification", "other"]
    reason: str = Field(min_length=10, max_length=2000)


class DisputeOut(BaseModel):
    id: str
    tenant_id: str
    meeting_id: str
    company: str
    kind: str
    reason: str
    status: str
    resolution_note: str | None
    created_at: datetime
    window_ends_at: datetime | None


@router.post("/disputes")
def raise_dispute(
    body: DisputeIn, user: Annotated[CurrentUser, Depends(require(Permission.RAISE_DISPUTE))]
) -> dict[str, str]:
    if user.role != "tenant_admin" or user.tenant_id is None:
        raise ProblemError(403, "Only the client can dispute a meeting")
    try:
        with transaction(user.db_context(), get_engine()) as conn:
            did = billing.raise_dispute(
                conn,
                tenant_id=user.tenant_id,
                meeting_id=body.meeting_id,
                user_id=user.id,
                kind=body.kind,
                reason=body.reason,
                now=datetime.now(UTC),
            )
    except billing.BillingError as exc:
        raise _err(exc) from exc
    return {"id": str(did), "status": "open"}


@router.get("/disputes", response_model=list[DisputeOut])
def list_disputes(user: Viewer, tenant_id: uuid.UUID | None = None) -> list[DisputeOut]:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        t = tenant_id if user.role == "operator" else user.tenant_id
        rows = (
            conn.execute(
                text("""SELECT d.*, c.name AS company FROM disputes d JOIN meetings m ON m.id = d.meeting_id
              JOIN leads l ON l.id = m.lead_id JOIN companies c ON c.id = l.company_id
              WHERE (CAST(:t AS uuid) IS NULL OR d.tenant_id = :t) ORDER BY (d.status = 'open') DESC, d.created_at DESC
              LIMIT 200"""),
                {"t": t},
            )
            .mappings()
            .all()
        )
        return [
            DisputeOut(
                id=str(r["id"]),
                tenant_id=str(r["tenant_id"]),
                meeting_id=str(r["meeting_id"]),
                company=r["company"] or "Sole trader",
                kind=r["kind"],
                reason=r["reason"],
                status=r["status"],
                resolution_note=r["resolution_note"],
                created_at=r["created_at"],
                window_ends_at=r["window_ends_at"],
            )
            for r in rows
        ]


class ResolveIn(BaseModel):
    outcome: Literal["upheld", "rejected"]
    note: str = Field(min_length=5, max_length=2000)


@router.post("/disputes/{dispute_id}/resolve")
def resolve(dispute_id: uuid.UUID, body: ResolveIn, user: Manager) -> dict[str, Any]:
    try:
        with transaction(user.db_context(), get_engine()) as conn:
            return billing.resolve_dispute(
                conn, dispute_id=dispute_id, user_id=user.id, outcome=body.outcome, note=body.note
            )
    except billing.BillingError as exc:
        raise _err(exc) from exc


# ------------------------------------------------------------------------------------------- tax and VAT
@router.get("/billing/vat-threshold")
def vat_threshold(user: Manager) -> dict[str, Any]:
    with transaction(user.db_context(), get_engine()) as conn:
        return billing.vat_threshold_status(conn, datetime.now(UTC).date())


class TaxRuleOut(BaseModel):
    code: str
    version: int
    rate_bps: int
    invoice_wording: str | None
    verified_by_accountant: bool
    last_reviewed: date | None
    source: str | None


@router.get("/tax-rules", response_model=list[TaxRuleOut])
def tax_rules(user: Manager) -> list[TaxRuleOut]:
    with transaction(user.db_context(), get_engine()) as conn:
        rows = conn.execute(text("SELECT DISTINCT ON (code) * FROM tax_rules ORDER BY code, version DESC")).mappings()
        return [TaxRuleOut(**{k: r[k] for k in TaxRuleOut.model_fields}) for r in rows]


class VerifyIn(MfaIn):
    accountant: str = Field(min_length=3, max_length=200)  # name and firm of the accountant who reviewed it
    reviewed_on: date


@router.post("/tax-rules/{code}/accountant-verification", status_code=201)
def verify_tax_rule(
    code: str,
    body: VerifyIn,
    user: Annotated[CurrentUser, Depends(require(Permission.SIGN_OFF_GOLIVE))],
    cipher: Cipher,
) -> dict[str, str]:
    if user.role != "operator":
        raise ProblemError(403, "Forbidden")
    try:
        with transaction(user.db_context(), get_engine()) as conn:
            _mfa(conn, cipher, user, body.totp_code)
            rid = billing.record_accountant_verification(
                conn, code=code, operator_id=user.id, accountant=body.accountant, reviewed_on=body.reviewed_on
            )
    except billing.BillingError as exc:
        raise _err(exc) from exc
    return {"id": str(rid), "status": "verified"}
