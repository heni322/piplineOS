"""Billing workflows: monthly draft invoices with an evidence chain per meeting, tax from versioned rules, a guarded
issue step with gapless numbering, disputes and credit lines, and VAT-threshold monitoring.

Nothing here sends anything: an issued invoice is a record. Issuing is blocked until go-live is signed off, the tax
rule has an accountant's sign-off and the supplier details are real (no placeholders)."""

from __future__ import annotations

import contextlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import RowMapping, text
from sqlalchemy.engine import Connection

from app.core.audit import write_audit
from app.domain.billing.eligibility import MeetingFacts, eligibility
from app.domain.billing.tax import TaxRuleRow, TaxUnresolved, select_rule, tax_facts, tax_minor
from app.domain.leads.state_machine import State, TransitionError, get_lead, transition
from app.domain.tenants import signoffs
from app.domain.tenants.config import Pricing, TenantConfig, load_config


class BillingError(Exception):
    def __init__(self, code: str, detail: str = "", reasons: list[str] | None = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.reasons = code, detail, reasons or []


@dataclass
class DraftResult:
    invoice_id: uuid.UUID | None
    lines: list[dict[str, Any]] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)  # meetings not billed, with reason codes
    blocked_reasons: list[str] = field(default_factory=list)  # why it can't be issued yet
    total_minor: int = 0


def month_bounds(period_start: date) -> tuple[date, date]:
    if period_start.day != 1:
        raise BillingError("BAD_PERIOD", "period must start on the first day of a month")
    nxt = date(period_start.year + (period_start.month == 12), period_start.month % 12 + 1, 1)
    return period_start, nxt - timedelta(days=1)


def money(minor: int, currency: str) -> str:
    sym = {"GBP": "£", "EUR": "€"}[currency]
    sign = "-" if minor < 0 else ""
    return f"{sign}{sym}{abs(minor) / 100:,.2f}"


# --------------------------------------------------------------------------------------------- facts
def _criteria_hash(conn: Connection, config_id: uuid.UUID | None) -> str | None:
    if config_id is None:
        return None
    raw = conn.execute(text("SELECT config FROM tenant_configs WHERE id = :c"), {"c": config_id}).scalar()
    if raw is None:
        return None
    content = signoffs.terms_of(TenantConfig.model_validate(raw), "qualification_criteria")
    return signoffs.terms_hash(content) if content else None


def meeting_facts(conn: Connection, tenant_id: uuid.UUID, meeting: RowMapping, pricing: Pricing | None) -> MeetingFacts:
    att = conn.execute(
        text("SELECT status, source FROM attendance_events WHERE meeting_id = :m ORDER BY recorded_at DESC LIMIT 1"),
        {"m": meeting["id"]},
    ).first()
    q = conn.execute(
        text("SELECT overall_met, criteria_config_id FROM qualifications WHERE lead_id = :l"), {"l": meeting["lead_id"]}
    ).first()
    billed = conn.execute(
        text("""SELECT 1 FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id
          WHERE l.meeting_id = :m AND l.kind = 'meeting' AND l.voided_at IS NULL
            AND i.status IN ('issued','paid')"""),
        {"m": meeting["id"]},
    ).first()
    dispute = conn.execute(
        text("SELECT status FROM disputes WHERE meeting_id = :m ORDER BY created_at DESC LIMIT 1"),
        {"m": meeting["id"]},
    ).scalar()
    return MeetingFacts(
        booked_by_system=bool(meeting["booked_by_system"]),
        meeting_status=meeting["status"],
        attendance_status=att.status if att else None,
        attendance_source=att.source if att else None,
        qualification_met=q.overall_met if q else None,
        criteria_hash=_criteria_hash(conn, q.criteria_config_id) if q else None,
        booked_at=meeting["created_at"],
        signed_criteria=signoffs.signed(conn, tenant_id, "qualification_criteria"),
        already_billed=billed is not None,
        per_meeting_minor=pricing.per_meeting_minor if pricing else 0,
        pricing_signed=signoffs.current_is_signed(conn, tenant_id, "pricing"),
        dispute_status=dispute,
    )


def evidence_chain(conn: Connection, meeting_id: uuid.UUID, lead_id: uuid.UUID) -> dict[str, Any]:
    """IDs only (no personal data), so the chain survives erasure of the prospect."""

    def ids(sql: str) -> list[str]:
        return [str(x) for x in conn.execute(text(sql), {"l": lead_id, "m": meeting_id}).scalars()]

    return {
        "signal_id": next(iter(ids("SELECT signal_id FROM leads WHERE id = :l AND signal_id IS NOT NULL")), None),
        "lead_id": str(lead_id),
        "message_ids": ids(
            "SELECT id FROM messages WHERE lead_id = :l AND status IN ('sent','dry_run_sent') ORDER BY created_at"
        ),
        "reply_ids": ids("SELECT id FROM replies WHERE lead_id = :l ORDER BY received_at"),
        "qualification_id": next(iter(ids("SELECT id FROM qualifications WHERE lead_id = :l")), None),
        "meeting_id": str(meeting_id),
        "attendance_event_id": next(
            iter(ids("SELECT id FROM attendance_events WHERE meeting_id = :m ORDER BY recorded_at DESC LIMIT 1")), None
        ),
    }


# --------------------------------------------------------------------------------------------- tax & guards
def _tax(conn: Connection, customer_country: str) -> tuple[TaxRuleRow | None, str | None]:
    reg = conn.execute(text("SELECT uk_vat_registered FROM tax_settings ORDER BY version DESC LIMIT 1")).scalar()
    rules = [
        TaxRuleRow(r.id, r.code, r.applies_when, r.rate_bps, r.invoice_wording, r.verified_by_accountant)
        for r in conn.execute(
            text("""SELECT DISTINCT ON (code) id, code, applies_when, rate_bps, invoice_wording, verified_by_accountant
              FROM tax_rules ORDER BY code, version DESC""")
        )
    ]
    if reg is None:
        return None, "NO_TAX_SETTINGS"
    try:
        return select_rule(rules, tax_facts(bool(reg), customer_country)), None
    except TaxUnresolved as exc:
        return None, exc.code


def _supplier(conn: Connection) -> RowMapping | None:
    return conn.execute(text("SELECT * FROM supplier_profiles ORDER BY version DESC LIMIT 1")).mappings().first()


def issue_blockers(conn: Connection, inv: RowMapping) -> list[str]:
    """Everything that must be true before an invoice can be issued. Unknown -> block."""
    reasons: list[str] = []
    if not conn.execute(text("SELECT golive_complete('uk')")).scalar():
        reasons.append("GO_LIVE_NOT_SIGNED_OFF")
    sup = _supplier(conn)
    if sup is None or sup["placeholder"]:
        reasons.append("SUPPLIER_DETAILS_INCOMPLETE")
    if inv["tax_rule_id"] is None:
        reasons.append("NO_TAX_RULE")
    elif not conn.execute(
        text("SELECT verified_by_accountant FROM tax_rules WHERE id = :r"), {"r": inv["tax_rule_id"]}
    ).scalar():
        reasons.append("TAX_RULE_NOT_VERIFIED_BY_ACCOUNTANT")
    if not signoffs.current_is_signed(conn, inv["tenant_id"], "pricing"):
        reasons.append("PRICING_NOT_SIGNED")
    if inv["total_minor"] == 0:
        reasons.append("NOTHING_TO_INVOICE")
    return reasons


# --------------------------------------------------------------------------------------------- drafts
def _void_draft(conn: Connection, invoice_id: uuid.UUID, reason: str) -> None:
    conn.execute(
        text("UPDATE invoice_lines SET voided_at = now() WHERE invoice_id = :i AND voided_at IS NULL"),
        {"i": invoice_id},
    )
    conn.execute(
        text("UPDATE invoices SET status = 'void', void_reason = :r WHERE id = :i AND status = 'draft'"),
        {"i": invoice_id, "r": reason},
    )


def refresh_draft(
    conn: Connection, *, tenant_id: uuid.UUID, period_start: date, today: date, actor_id: str = "system"
) -> DraftResult:
    """(Re)builds the draft invoice for a calendar month. Idempotent: an existing draft is voided and rebuilt;
    a month that already has an issued invoice is refused (later changes become credits on the next one)."""
    start, end = month_bounds(period_start)
    existing = conn.execute(
        text(
            "SELECT id, status FROM invoices WHERE tenant_id = :t AND period_start = :p AND status <> 'void' FOR UPDATE"
        ),
        {"t": tenant_id, "p": start},
    ).first()
    if existing and existing.status != "draft":
        raise BillingError("ALREADY_ISSUED", f"{start:%B %Y} already has an issued invoice")
    if existing:
        _void_draft(conn, existing.id, "regenerated")

    config_id, cfg = load_config(conn, tenant_id)
    tenant = conn.execute(text("SELECT * FROM tenants WHERE id = :t"), {"t": tenant_id}).mappings().one()
    pricing = cfg.pricing
    currency = tenant["billing_currency"]
    res = DraftResult(None)
    if pricing is None:
        res.blocked_reasons.append("NO_PRICING")
        return res
    if pricing.currency != currency:
        res.blocked_reasons.append("CURRENCY_MISMATCH")
        return res

    lines: list[dict[str, Any]] = []
    if (
        pricing.setup_fee_minor > 0
        and not conn.execute(
            text("""SELECT 1 FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id
          WHERE l.tenant_id = :t AND l.kind = 'setup' AND l.voided_at IS NULL AND i.status <> 'void'"""),
            {"t": tenant_id},
        ).first()
    ):
        lines.append(
            {
                "kind": "setup",
                "description": "Set-up fee",
                "amount_minor": pricing.setup_fee_minor,
                "meeting_id": None,
                "evidence": {},
                "credit_for_line_id": None,
            }
        )
    if pricing.monthly_retainer_minor > 0:
        lines.append(
            {
                "kind": "retainer",
                "description": f"Monthly service, {start:%B %Y}",
                "amount_minor": pricing.monthly_retainer_minor,
                "meeting_id": None,
                "evidence": {},
                "credit_for_line_id": None,
            }
        )

    period_end_ts = datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)
    meetings = (
        conn.execute(
            text("""SELECT m.*, c.name AS company FROM meetings m JOIN leads l ON l.id = m.lead_id
          JOIN companies c ON c.id = l.company_id
          WHERE m.status IN ('attended','no_show') AND m.ends_at < :e ORDER BY m.starts_at"""),
            {"e": period_end_ts},
        )
        .mappings()
        .all()
    )
    for m in meetings:
        facts = meeting_facts(conn, tenant_id, m, pricing)
        ok, reasons = eligibility(facts)
        label = f"{m['company'] or 'Sole trader'}, {m['starts_at']:%d %b %Y}"
        if not ok:
            if "ALREADY_BILLED" not in reasons:
                res.excluded.append({"meeting_id": str(m["id"]), "label": label, "reasons": reasons})
            continue
        lines.append(
            {
                "kind": "meeting",
                "description": f"Qualified meeting attended: {label}",
                "amount_minor": pricing.per_meeting_minor,
                "meeting_id": m["id"],
                "evidence": evidence_chain(conn, m["id"], m["lead_id"]),
                "credit_for_line_id": None,
            }
        )

    # Upheld disputes on lines that were already issued -> credit lines (credit notes) on this draft.
    for d in conn.execute(
        text("""SELECT d.id AS dispute_id, l.id AS line_id, l.amount_minor, l.description, i.number
          FROM disputes d JOIN invoice_lines l ON l.meeting_id = d.meeting_id AND l.kind = 'meeting'
          JOIN invoices i ON i.id = l.invoice_id
          WHERE d.status = 'upheld' AND l.voided_at IS NULL AND i.status <> 'void' AND i.status IN ('issued','paid')
            AND NOT EXISTS (SELECT 1 FROM invoice_lines c JOIN invoices ci ON ci.id = c.invoice_id
                            WHERE c.credit_for_line_id = l.id AND c.voided_at IS NULL AND ci.status <> 'void')""")
    ).mappings():
        lines.append(
            {
                "kind": "credit",
                "description": f"Credit: {d['description']} (invoice {d['number']}, dispute upheld)",
                "amount_minor": -int(d["amount_minor"]),
                "meeting_id": None,
                "evidence": {"dispute_id": str(d["dispute_id"])},
                "credit_for_line_id": d["line_id"],
            }
        )

    if not lines:
        res.blocked_reasons.append("NOTHING_TO_INVOICE")
        return res

    customer_country = tenant["country_code"]
    rule, tax_problem = _tax(conn, customer_country)
    subtotal = sum(int(x["amount_minor"]) for x in lines)
    tax = tax_minor(subtotal, rule.rate_bps) if rule else 0
    sup = _supplier(conn)
    supplier_snapshot = (
        {
            k: sup[k]
            for k in (
                "legal_name",
                "company_number",
                "registered_address",
                "country_code",
                "vat_number",
                "billing_email",
                "payment_details",
            )
        }
        | {"profile_version": sup["version"]}
        if sup
        else {}
    )
    customer_snapshot = {
        "legal_name": tenant["legal_name"],
        "company_number": tenant["company_number"],
        "registered_address": tenant["registered_address"],
        "country_code": customer_country,
        "vat_number": tenant["vat_number"],
        "pricing_config_id": str(config_id),
    }
    inv_id: uuid.UUID = conn.execute(
        text("""INSERT INTO invoices (tenant_id, number, issue_date, due_date, currency, supplier_snapshot,
          customer_snapshot, place_of_supply, tax_rule_id, tax_wording, subtotal_minor, tax_minor, total_minor,
          status, period_start, period_end, customer_country)
          VALUES (:t, :n, :d, :due, :c, CAST(:ss AS jsonb), CAST(:cs AS jsonb), :pos, :tr, :tw, :sub, :tax, :tot,
          'draft', :ps, :pe, :cc) RETURNING id"""),
        {
            "t": tenant_id,
            "n": f"DRAFT-{uuid.uuid4().hex[:10].upper()}",
            "d": today,
            "due": today + timedelta(days=pricing.payment_terms_days),
            "c": currency,
            "ss": json.dumps(supplier_snapshot),
            "cs": json.dumps(customer_snapshot),
            "pos": customer_country,
            "tr": rule.id if rule else None,
            "tw": rule.wording if rule else None,
            "sub": subtotal,
            "tax": tax,
            "tot": subtotal + tax,
            "ps": start,
            "pe": end,
            "cc": customer_country,
        },
    ).scalar_one()
    for x in lines:
        conn.execute(
            text("""INSERT INTO invoice_lines (tenant_id, invoice_id, kind, meeting_id, description, amount_minor,
              evidence, credit_for_line_id) VALUES (:t, :i, :k, :m, :d, :a, CAST(:e AS jsonb), :cf)"""),
            {
                "t": tenant_id,
                "i": inv_id,
                "k": x["kind"],
                "m": x["meeting_id"],
                "d": x["description"],
                "a": x["amount_minor"],
                "e": json.dumps(x["evidence"]),
                "cf": x["credit_for_line_id"],
            },
        )
    inv = conn.execute(text("SELECT * FROM invoices WHERE id = :i"), {"i": inv_id}).mappings().one()
    blockers = ([tax_problem] if tax_problem else []) + [b for b in issue_blockers(conn, inv) if b != tax_problem]
    conn.execute(text("UPDATE invoices SET blocked_reasons = :b WHERE id = :i"), {"b": blockers, "i": inv_id})
    write_audit(
        conn,
        action="invoice.drafted",
        actor_type="system" if actor_id == "system" else "user",
        actor_id=actor_id,
        tenant_id=tenant_id,
        entity_type="invoice",
        entity_id=inv_id,
        after={"period": start.isoformat(), "total_minor": subtotal + tax, "lines": len(lines)},
    )
    res.invoice_id, res.blocked_reasons, res.total_minor = inv_id, blockers, subtotal + tax
    res.lines = [
        {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in x.items() if k != "evidence"} for x in lines
    ]
    return res


# --------------------------------------------------------------------------------------------- issue
def _next_number(conn: Connection, prefix: str, year: int) -> str:
    conn.execute(
        text("INSERT INTO invoice_counters (prefix, year) VALUES (:p, :y) ON CONFLICT DO NOTHING"),
        {"p": prefix, "y": year},
    )
    n = conn.execute(
        text(
            "UPDATE invoice_counters SET last_number = last_number + 1 WHERE prefix = :p AND year = :y "
            "RETURNING last_number"
        ),
        {"p": prefix, "y": year},
    ).scalar_one()
    return f"{prefix}-{year}-{n:04d}"


def issue(conn: Connection, *, invoice_id: uuid.UUID, user_id: uuid.UUID, today: date) -> str:
    inv = conn.execute(text("SELECT * FROM invoices WHERE id = :i FOR UPDATE"), {"i": invoice_id}).mappings().first()
    if inv is None:
        raise BillingError("NOT_FOUND")
    if inv["status"] != "draft":
        raise BillingError("NOT_A_DRAFT", inv["status"])
    blockers = issue_blockers(conn, inv)
    if blockers:
        conn.execute(text("UPDATE invoices SET blocked_reasons = :b WHERE id = :i"), {"b": blockers, "i": invoice_id})
        raise BillingError("ISSUE_BLOCKED", ", ".join(blockers), blockers)
    sup = _supplier(conn)
    if sup is None:  # guarded by issue_blockers; kept explicit
        raise BillingError("ISSUE_BLOCKED", "SUPPLIER_DETAILS_INCOMPLETE")
    _, cfg = load_config(conn, inv["tenant_id"])
    terms = cfg.pricing.payment_terms_days if cfg.pricing else 14
    number = _next_number(conn, sup["invoice_prefix"], today.year)
    conn.execute(
        text("""UPDATE invoices SET number = :n, status = 'issued', issue_date = :d, due_date = :due,
          issued_at = now(), issued_by = :u, blocked_reasons = '{}' WHERE id = :i"""),
        {"n": number, "d": today, "due": today + timedelta(days=terms), "u": user_id, "i": invoice_id},
    )
    for lead_id in conn.execute(
        text("""SELECT m.lead_id FROM invoice_lines l JOIN meetings m ON m.id = l.meeting_id
          WHERE l.invoice_id = :i AND l.kind = 'meeting' AND l.voided_at IS NULL"""),
        {"i": invoice_id},
    ).scalars():
        if get_lead(conn, lead_id).state == State.ATTENDED:
            transition(
                conn, lead_id, State.BILLED, actor_type="user", actor_id=str(user_id), reason=f"invoice {number}"
            )
    write_audit(
        conn,
        action="invoice.issued",
        actor_type="user",
        actor_id=str(user_id),
        tenant_id=inv["tenant_id"],
        entity_type="invoice",
        entity_id=invoice_id,
        after={"number": number},
    )
    return number


def void_issued(conn: Connection, *, invoice_id: uuid.UUID, user_id: uuid.UUID, reason: str) -> None:
    """An issued invoice is never edited or deleted; it can only be voided (with a reason) and replaced."""
    conn.execute(
        text("UPDATE invoices SET status = 'void', void_reason = :r WHERE id = :i AND status = 'issued'"),
        {"r": reason, "i": invoice_id},
    )
    write_audit(
        conn,
        action="invoice.voided",
        actor_type="user",
        actor_id=str(user_id),
        tenant_id=None,
        entity_type="invoice",
        entity_id=invoice_id,
        after={"reason": reason},
    )


# --------------------------------------------------------------------------------------------- disputes
def raise_dispute(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    meeting_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str,
    reason: str,
    now: datetime,
) -> uuid.UUID:
    m = conn.execute(text("SELECT * FROM meetings WHERE id = :m"), {"m": meeting_id}).mappings().first()
    if m is None:
        raise BillingError("NOT_FOUND")
    if m["status"] != "attended":
        raise BillingError("NOT_DISPUTABLE", "only attended meetings can be disputed")
    att = conn.execute(
        text("SELECT recorded_at FROM attendance_events WHERE meeting_id = :m ORDER BY recorded_at DESC LIMIT 1"),
        {"m": meeting_id},
    ).scalar()
    _, cfg = load_config(conn, tenant_id)
    window = cfg.pricing.dispute_window_days if cfg.pricing else 14
    ends = (att or m["ends_at"]) + timedelta(days=window)
    if now > ends:
        raise BillingError("DISPUTE_WINDOW_CLOSED", f"closed on {ends:%d %b %Y}")
    if conn.execute(
        text("SELECT 1 FROM disputes WHERE meeting_id = :m AND status = 'open'"), {"m": meeting_id}
    ).first():
        raise BillingError("ALREADY_DISPUTED")
    line = conn.execute(
        text("""SELECT l.id FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id
          WHERE l.meeting_id = :m AND l.kind = 'meeting' AND l.voided_at IS NULL AND i.status <> 'void'"""),
        {"m": meeting_id},
    ).scalar()
    did: uuid.UUID = conn.execute(
        text("""INSERT INTO disputes (tenant_id, meeting_id, invoice_line_id, raised_by, reason, kind, window_ends_at)
          VALUES (:t, :m, :l, :u, :r, :k, :w) RETURNING id"""),
        {"t": tenant_id, "m": meeting_id, "l": line, "u": user_id, "r": reason, "k": kind, "w": ends},
    ).scalar_one()
    lead = get_lead(conn, m["lead_id"])
    if lead.state in (State.ATTENDED, State.BILLED):
        transition(
            conn, m["lead_id"], State.DISPUTED, actor_type="user", actor_id=str(user_id), reason=f"dispute: {kind}"
        )
    write_audit(
        conn,
        action="dispute.raised",
        actor_type="user",
        actor_id=str(user_id),
        tenant_id=tenant_id,
        entity_type="dispute",
        entity_id=did,
        after={"kind": kind},
    )
    return did


def resolve_dispute(
    conn: Connection, *, dispute_id: uuid.UUID, user_id: uuid.UUID, outcome: str, note: str
) -> dict[str, Any]:
    d = conn.execute(text("SELECT * FROM disputes WHERE id = :d FOR UPDATE"), {"d": dispute_id}).mappings().first()
    if d is None:
        raise BillingError("NOT_FOUND")
    if d["status"] != "open":
        raise BillingError("ALREADY_RESOLVED")
    conn.execute(
        text("""UPDATE disputes SET status = :s, resolution_note = :n, resolved_by = :u, resolved_at = now()
          WHERE id = :d"""),
        {"s": outcome, "n": note, "u": user_id, "d": dispute_id},
    )
    line = conn.execute(
        text("""SELECT l.id, i.status AS invoice_status, l.invoice_id FROM invoice_lines l
          JOIN invoices i ON i.id = l.invoice_id
          WHERE l.meeting_id = :m AND l.kind = 'meeting' AND l.voided_at IS NULL AND i.status <> 'void'"""),
        {"m": d["meeting_id"]},
    ).first()
    effect = "none"
    if outcome == "upheld" and line is not None and line.invoice_status == "draft":
        conn.execute(text("UPDATE invoice_lines SET voided_at = now() WHERE id = :l"), {"l": line.id})
        _recompute_draft(conn, line.invoice_id)
        effect = "removed_from_draft"
    elif outcome == "upheld" and line is not None:
        effect = "credit_on_next_invoice"
    lead_id = conn.execute(text("SELECT lead_id FROM meetings WHERE id = :m"), {"m": d["meeting_id"]}).scalar_one()
    issued = line is not None and line.invoice_status in ("issued", "paid")
    target = State.BILLED if (outcome == "rejected" and issued) else State.ATTENDED
    # lead moved on (e.g. erased); the dispute record is what billing reads
    with contextlib.suppress(TransitionError):
        transition(
            conn, lead_id, target, actor_type="user", actor_id=str(user_id), reason=f"dispute {outcome}: {note[:80]}"
        )
    write_audit(
        conn,
        action=f"dispute.{outcome}",
        actor_type="user",
        actor_id=str(user_id),
        tenant_id=d["tenant_id"],
        entity_type="dispute",
        entity_id=dispute_id,
        after={"effect": effect},
    )
    return {"status": outcome, "effect": effect}


def _recompute_draft(conn: Connection, invoice_id: uuid.UUID) -> None:
    inv = conn.execute(text("SELECT tax_rule_id FROM invoices WHERE id = :i"), {"i": invoice_id}).first()
    rate = 0
    if inv and inv.tax_rule_id:
        rate = int(
            conn.execute(text("SELECT rate_bps FROM tax_rules WHERE id = :r"), {"r": inv.tax_rule_id}).scalar_one()
        )
    sub = int(
        conn.execute(
            text(
                "SELECT coalesce(sum(amount_minor), 0) FROM invoice_lines WHERE invoice_id = :i AND voided_at IS NULL"
            ),
            {"i": invoice_id},
        ).scalar_one()
    )
    tax = tax_minor(sub, rate)
    conn.execute(
        text(
            "UPDATE invoices SET subtotal_minor = :s, tax_minor = :t, total_minor = :tot WHERE id = :i "
            "AND status = 'draft'"
        ),
        {"s": sub, "t": tax, "tot": sub + tax, "i": invoice_id},
    )


# --------------------------------------------------------------------------------------------- VAT threshold
def vat_threshold_status(conn: Connection, today: date) -> dict[str, Any]:
    """Rolling 12-month taxable turnover (issued invoices, GBP) against the configured registration threshold."""
    s = conn.execute(
        text("""SELECT uk_vat_registered, uk_vat_registration_threshold_minor, warn_at_pct FROM tax_settings
          ORDER BY version DESC LIMIT 1""")
    ).first()
    turnover = int(
        conn.execute(
            text("""SELECT coalesce(sum(subtotal_minor), 0) FROM invoices WHERE status IN ('issued','paid')
          AND currency = 'GBP' AND issue_date > :since"""),
            {"since": today - timedelta(days=365)},
        ).scalar_one()
    )
    other = int(
        conn.execute(
            text(
                "SELECT count(*) FROM invoices WHERE status IN ('issued','paid') AND currency <> 'GBP' "
                "AND issue_date > :since"
            ),
            {"since": today - timedelta(days=365)},
        ).scalar_one()
    )
    if s is None or s.uk_vat_registration_threshold_minor is None:
        return {
            "turnover_minor": turnover,
            "threshold_minor": None,
            "pct": None,
            "warning": "THRESHOLD_UNKNOWN",
            "registered": bool(s and s.uk_vat_registered),
            "non_gbp_invoices": other,
        }
    pct = round(100 * turnover / s.uk_vat_registration_threshold_minor, 1)
    level = max([w for w in s.warn_at_pct if pct >= w], default=None)
    return {
        "turnover_minor": turnover,
        "threshold_minor": s.uk_vat_registration_threshold_minor,
        "pct": pct,
        "warning": None if s.uk_vat_registered or level is None else f"AT_{level}_PCT",
        "registered": s.uk_vat_registered,
        "non_gbp_invoices": other,
    }


# --------------------------------------------------------------------------------------------- accountant sign-off
def record_accountant_verification(
    conn: Connection, *, code: str, operator_id: uuid.UUID, accountant: str, reviewed_on: date
) -> uuid.UUID:
    """The owner records that their accountant reviewed this rule: a new, verified version of the same content.
    Editing the rule in config later creates a fresh unverified version, so the sign-off never carries over."""
    cur = (
        conn.execute(text("SELECT * FROM tax_rules WHERE code = :c ORDER BY version DESC LIMIT 1"), {"c": code})
        .mappings()
        .first()
    )
    if cur is None:
        raise BillingError("NOT_FOUND", code)
    if cur["verified_by_accountant"]:
        raise BillingError("ALREADY_VERIFIED", code)
    rid: uuid.UUID = conn.execute(
        text("""INSERT INTO tax_rules (code, version, content_hash, applies_when, rate_bps, invoice_wording,
          verified_by_accountant, verified_by, last_reviewed, source)
          VALUES (:c, :v, :h, CAST(:a AS jsonb), :r, :w, true, :u, :d, :s) RETURNING id"""),
        {
            "c": code,
            "v": cur["version"] + 1,
            "h": cur["content_hash"],
            "a": json.dumps(cur["applies_when"]),
            "r": cur["rate_bps"],
            "w": cur["invoice_wording"],
            "u": operator_id,
            "d": reviewed_on,
            "s": f"{cur['source']} | reviewed by {accountant} on {reviewed_on.isoformat()}",
        },
    ).scalar_one()
    write_audit(
        conn,
        action="tax_rule.accountant_verified",
        actor_type="user",
        actor_id=str(operator_id),
        tenant_id=None,
        entity_type="tax_rule",
        entity_id=rid,
        after={"code": code, "accountant": accountant},
    )
    return rid
