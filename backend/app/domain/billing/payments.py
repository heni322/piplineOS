"""Money received against issued invoices. Append-only entries; the invoice turns 'paid' once the net received
covers its total. Overpayments are recorded as received (and shown as a credit balance); they are never hidden."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.audit import write_audit


class PaymentError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Balance:
    total_minor: int
    paid_minor: int

    @property
    def due_minor(self) -> int:
        return self.total_minor - self.paid_minor


def balance(conn: Connection, invoice_id: uuid.UUID) -> Balance:
    r = conn.execute(
        text("""SELECT i.total_minor, coalesce((SELECT sum(amount_minor) FROM invoice_payments p
                WHERE p.invoice_id = i.id), 0) AS paid FROM invoices i WHERE i.id = :i"""),
        {"i": invoice_id},
    ).one()
    return Balance(int(r.total_minor), int(r.paid))


def record_payment(
    conn: Connection,
    *,
    invoice_id: uuid.UUID,
    user_id: uuid.UUID,
    amount_minor: int,
    received_on: date,
    method: str,
    reference: str | None,
    today: date,
) -> Balance:
    inv = conn.execute(
        text("SELECT tenant_id, status, currency, issue_date FROM invoices WHERE id = :i FOR UPDATE"),
        {"i": invoice_id},
    ).first()
    if inv is None:
        raise PaymentError("NOT_FOUND")
    if inv.status not in ("issued", "paid"):
        raise PaymentError("NOT_ISSUED")
    if amount_minor == 0:
        raise PaymentError("ZERO_AMOUNT")
    if (method == "reversal") != (amount_minor < 0):
        raise PaymentError("REVERSAL_MUST_BE_NEGATIVE")
    if received_on > today or received_on < inv.issue_date:
        raise PaymentError("DATE_OUT_OF_RANGE")
    before = balance(conn, invoice_id)
    if amount_minor < 0 and before.paid_minor + amount_minor < 0:
        raise PaymentError("REVERSAL_EXCEEDS_RECEIVED")
    if inv.status == "paid" and amount_minor < 0:
        # A paid invoice stays paid (issued invoices only move forward); a refund is a credit note, outside here.
        raise PaymentError("INVOICE_ALREADY_PAID")
    conn.execute(
        text("""INSERT INTO invoice_payments (tenant_id, invoice_id, amount_minor, currency, received_on, method,
                reference, recorded_by) VALUES (:t, :i, :a, :c, :d, :m, :r, :u)"""),
        {
            "t": inv.tenant_id,
            "i": invoice_id,
            "a": amount_minor,
            "c": inv.currency,
            "d": received_on,
            "m": method,
            "r": (reference or "").strip()[:100] or None,
            "u": user_id,
        },
    )
    after = balance(conn, invoice_id)
    if inv.status == "issued" and after.due_minor <= 0:
        conn.execute(text("UPDATE invoices SET status = 'paid' WHERE id = :i AND status = 'issued'"), {"i": invoice_id})
    write_audit(
        conn,
        action="invoice.payment_recorded",
        actor_type="user",
        actor_id=str(user_id),
        tenant_id=inv.tenant_id,
        entity_type="invoice",
        entity_id=invoice_id,
        after={
            "amount_minor": amount_minor,
            "method": method,
            "received_on": received_on.isoformat(),
            "paid_minor": after.paid_minor,
            "due_minor": after.due_minor,
        },
    )
    return after
