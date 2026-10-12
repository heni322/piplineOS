"""Client and operator reporting. Read-only SQL; every query filters by tenant explicitly (operators can see all
tenants under RLS, so the filter is what keeps one client's numbers apart from another's)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def metrics(conn: Connection, tenant_id: uuid.UUID, start: date, end: date) -> dict[str, Any]:
    """Counts for leads/messages/replies/meetings created in [start, end] (inclusive dates, UTC)."""
    p = {
        "t": tenant_id,
        "s": datetime(start.year, start.month, start.day),
        "e": datetime(end.year, end.month, end.day) + timedelta(days=1),
    }

    def one(sql: str) -> int:
        return int(conn.execute(text(sql), p).scalar_one() or 0)

    discovered = one("SELECT count(*) FROM leads WHERE tenant_id = :t AND created_at >= :s AND created_at < :e")
    contacted = one("""SELECT count(DISTINCT lead_id) FROM messages WHERE tenant_id = :t AND kind = 'outreach'
        AND sequence_step = 0 AND status IN ('sent','dry_run_sent') AND sent_at >= :s AND sent_at < :e""")
    replies = one("""SELECT count(DISTINCT lead_id) FROM replies WHERE tenant_id = :t AND lead_id IS NOT NULL
        AND received_at >= :s AND received_at < :e""")
    positive = one("""SELECT count(DISTINCT lead_id) FROM replies WHERE tenant_id = :t AND classification = 'interested'
        AND received_at >= :s AND received_at < :e""")
    booked = one("""SELECT count(*) FROM meetings WHERE tenant_id = :t AND booked_by_system AND status <> 'proposed'
        AND created_at >= :s AND created_at < :e""")
    held = conn.execute(
        text("""SELECT status, count(*) FROM meetings WHERE tenant_id = :t
        AND status IN ('attended','no_show') AND starts_at >= :s AND starts_at < :e GROUP BY status"""),
        p,
    ).all()
    attended = next((n for s, n in held if s == "attended"), 0)
    no_show = next((n for s, n in held if s == "no_show"), 0)
    billed = conn.execute(
        text("""SELECT count(*), coalesce(sum(l.amount_minor), 0) FROM invoice_lines l
        JOIN invoices i ON i.id = l.invoice_id WHERE l.tenant_id = :t AND l.kind = 'meeting'
        AND l.voided_at IS NULL AND i.status IN ('issued','paid') AND i.issue_date >= :s AND i.issue_date < :e"""),
        p,
    ).one()
    cost = conn.execute(
        text("""SELECT count(*), count(cost_micros_gbp), coalesce(sum(cost_micros_gbp), 0)
        FROM llm_calls WHERE tenant_id = :t AND created_at >= :s AND created_at < :e"""),
        p,
    ).one()
    calls, priced, micros = int(cost[0]), int(cost[1]), int(cost[2])
    cost_known = calls == priced
    blocks = conn.execute(
        text("""SELECT r AS reason, count(*) FROM compliance_decisions d, unnest(d.reason_codes) r
        WHERE d.tenant_id = :t AND d.outcome <> 'allow' AND d.created_at >= :s AND d.created_at < :e
        GROUP BY r ORDER BY 2 DESC"""),
        p,
    ).all()
    esc = conn.execute(
        text("""SELECT reason, count(*) FROM escalations WHERE tenant_id = :t
        AND created_at >= :s AND created_at < :e GROUP BY reason ORDER BY 2 DESC"""),
        p,
    ).all()
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "leads_discovered": discovered,
        "contacted": contacted,
        "replied": replies,
        "positive_replies": positive,
        "reply_rate": _ratio(replies, contacted),
        "positive_reply_rate": _ratio(positive, contacted),
        "meetings_booked": booked,
        "meetings_attended": attended,
        "no_shows": no_show,
        "show_rate": _ratio(attended, attended + no_show),
        "meetings_billed": int(billed[0]),
        "billed_minor": int(billed[1]),
        "ai_calls": calls,
        # Cost is only reported when every call has a verified price; otherwise it says so rather than understating.
        "ai_cost_gbp": round(micros / 1_000_000, 2) if cost_known and calls else None,
        "ai_cost_per_meeting_gbp": round(micros / 1_000_000 / attended, 2) if cost_known and attended else None,
        "ai_cost_status": "ok" if cost_known else "prices_not_configured",
        "compliance_blocks": {r: int(n) for r, n in blocks},
        "escalations": {r: int(n) for r, n in esc},
    }
