"""The weekly results email to each client's admins: last week's numbers per 100 people reached, in plain words.

Sent on Mondays (the daily job does nothing on other days), at most once per person per week (idempotency key),
through the same channel as invoice notices: recorded in dry-run until the client and global go-live are live.
Clients with no activity in the week get nothing. Demo (dry-run) numbers say so in the first line."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.crypto import PiiCipher
from app.db.session import DbContext, transaction
from app.domain.reporting.metrics import metrics
from app.settings import Settings


def _per100(v: float | None) -> str:
    return "–" if v is None else f"{v:.1f}"


def compose(m: dict[str, Any], start: date, end: date, portal: str) -> tuple[str, str] | None:
    """(subject, body) for one client's week, or None when nothing happened."""
    if not (m["emails_sent"] or m["replied"] or m["meetings_booked"] or m["meetings_attended"]):
        return None
    p = m["per_100_reached"]
    lines = []
    if m["data_mode"] == "dry_run":
        lines.append("These are DEMO numbers: emails were recorded in dry-run, not sent.\n")
    elif m["data_mode"] == "mixed":
        lines.append("Some of these emails were dry-run records, not real sends.\n")
    lines += [
        f"Your PipelineOS week, {start:%d %b} to {end:%d %b %Y}:",
        "",
        f"- People reached: {m['people_reached']}",
        f"- Replies: {m['replied']} ({_per100(p['replies'])} per 100 people reached)",
        f"- Positive replies: {m['positive_replies']} ({_per100(p['positive_replies'])} per 100)",
        f"- Meetings booked: {m['meetings_booked']}",
        f"- Meetings held: {m['meetings_attended']}, of which qualified: {m['qualified_meetings']}",
        f"- Bounces: {m['bounces']}, spam complaints: {m['complaints']}",
        "",
        f"Every meeting, with the evidence behind it, is in your portal: {portal}/client",
        "If anything looks wrong, you can stop all sending from the portal at any time.",
    ]
    meetings = m["meetings_booked"]
    subject = f"Your week: {m['people_reached']} reached, {meetings} meeting{'s' if meetings != 1 else ''} booked"
    return subject, "\n".join(lines)


def weekly_digest(engine: Engine, settings: Settings, cipher: PiiCipher, now: datetime | None = None) -> int:
    """Send last week's results (Monday to Sunday) on a Monday. Returns how many emails were sent or recorded."""
    from app.domain.outbound import system_mail

    at = (now or datetime.now(UTC)).astimezone(UTC)
    if at.weekday() != 0:
        return 0
    end = at.date() - timedelta(days=1)
    start = end - timedelta(days=6)
    week = f"{start.isocalendar().year}-W{start.isocalendar().week:02d}"
    with transaction(DbContext.system(), engine) as conn:
        tenant_ids = (
            conn.execute(text("SELECT id FROM tenants WHERE status = 'active' AND NOT is_operator_own")).scalars().all()
        )
    sent = 0
    for tid in tenant_ids:
        with transaction(DbContext.system(tid), engine) as conn:
            content = compose(metrics(conn, tid, start, end), start, end, settings.public_base_url)
            if content is None:
                continue
            subject, body = content
            for uid, enc in conn.execute(
                text(
                    "SELECT id, email_enc FROM users WHERE tenant_id = :t AND role = 'tenant_admin' "
                    "AND status = 'active'"
                ),
                {"t": tid},
            ).all():
                system_mail.send(
                    conn,
                    settings,
                    cipher,
                    tenant_id=tid,
                    kind="weekly_report",
                    to_email=cipher.decrypt(conn, None, "users.email", enc),
                    subject=subject,
                    body=body,
                    idempotency_key=f"weekly:{tid}:{week}:{uid}",
                    entity_id=uuid.UUID(str(tid)),
                )
                sent += 1
    return sent
