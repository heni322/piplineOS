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


def two_proportion_p(x1: int, n1: int, x2: int, n2: int) -> float | None:
    """Two-sided p-value of a two-proportion z-test (normal approximation); None when either group is empty."""
    import math

    if not n1 or not n2:
        return None
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = abs(x1 / n1 - x2 / n2) / se
    return round(math.erfc(z / math.sqrt(2)), 4)


MIN_PER_VARIANT = 100  # people reached per variant before any comparison is shown as meaningful


def subject_test(conn: Connection, p: dict[str, Any]) -> dict[str, Any] | None:
    """Per subject-line variant: people reached, replies and positive replies per 100 reached, and whether the
    best variant's lead on positive replies is statistically meaningful (p < 0.05 with enough people)."""
    rows = conn.execute(
        text("""SELECT m.subject_variant AS v, count(DISTINCT m.lead_id) AS reached,
               count(DISTINCT r.lead_id) AS replied,
               count(DISTINCT r.lead_id) FILTER (WHERE r.classification = 'interested') AS positive
        FROM messages m LEFT JOIN replies r ON r.lead_id = m.lead_id
        WHERE m.tenant_id = :t AND m.kind = 'outreach' AND m.sequence_step = 0 AND m.subject_variant IS NOT NULL
          AND m.status IN ('sent','dry_run_sent','complained') AND m.sent_at >= :s AND m.sent_at < :e
        GROUP BY m.subject_variant ORDER BY m.subject_variant"""),
        p,
    ).all()
    if not rows:
        return None
    counts = [(int(r.v), int(r.reached), int(r.replied), int(r.positive)) for r in rows]
    variants = [
        {
            "variant": v,
            "reached": n,
            "replied": rep,
            "positive": pos,
            "replies_per_100": _per100(rep, n),
            "positive_per_100": _per100(pos, n),
        }
        for v, n, rep, pos in counts
    ]
    ranked = sorted(counts, key=lambda c: c[3] / c[1] if c[1] else 0.0, reverse=True)
    enough = len(counts) > 1 and all(c[1] >= MIN_PER_VARIANT for c in counts)
    pval = two_proportion_p(ranked[0][3], ranked[0][1], ranked[1][3], ranked[1][1]) if len(ranked) > 1 else None
    significant = enough and pval is not None and pval < 0.05
    return {
        "variants": variants,
        "leader": ranked[0][0] if significant else None,
        "p_value": pval,
        "verdict": "significant" if significant else ("not_significant" if enough else "not_enough_data"),
        "min_reached_per_variant": MIN_PER_VARIANT,
    }


def _per100(n: int, base: int) -> float | None:
    return round(100 * n / base, 1) if base else None


def by_source(conn: Connection, p: dict[str, Any]) -> list[dict[str, Any]]:
    """Data quality per signal source, for the leads found in the period (referrals excluded): how many the register
    confirmed, how many had a proven website and an email address, how many the compliance gate blocked or turned
    into contact tasks, and, of those emailed, how many were reached, bounced and replied. Lead states are the
    current ones. This is the measurement a pilot needs: which source is worth its noise."""
    rows = conn.execute(
        text("""
        WITH l AS (
          SELECT l.id, l.state, l.prospect_id, s.source_adapter AS source, c.company_number, c.domain
          FROM leads l JOIN signals s ON s.id = l.signal_id JOIN companies c ON c.id = l.company_id
          WHERE l.tenant_id = :t AND l.created_at >= :s AND l.created_at < :e AND l.referred_from_lead_id IS NULL),
        m AS (
          SELECT lead_id, bool_or(status <> 'bounced') AS reached, bool_or(status = 'bounced') AS bounced
          FROM messages WHERE tenant_id = :t AND kind = 'outreach' AND sequence_step = 0
          AND status IN ('sent', 'dry_run_sent', 'bounced', 'complained') GROUP BY lead_id),
        r AS (
          SELECT lead_id, bool_or(classification = 'interested') AS positive
          FROM replies WHERE tenant_id = :t AND lead_id IS NOT NULL GROUP BY lead_id)
        SELECT l.source, count(*) AS leads,
          count(*) FILTER (WHERE l.company_number IS NOT NULL) AS register_confirmed,
          count(*) FILTER (WHERE l.domain IS NOT NULL) AS with_website,
          count(*) FILTER (WHERE p.email_enc IS NOT NULL) AS with_email,
          count(*) FILTER (WHERE l.state = 'rejected_by_compliance') AS blocked,
          count(*) FILTER (WHERE l.state = 'alternative_channel') AS contact_tasks,
          count(*) FILTER (WHERE m.reached) AS reached,
          count(*) FILTER (WHERE m.bounced AND NOT m.reached) AS bounced,
          count(r.lead_id) AS replied,
          count(*) FILTER (WHERE r.positive) AS positive
        FROM l LEFT JOIN prospects p ON p.id = l.prospect_id LEFT JOIN m ON m.lead_id = l.id
        LEFT JOIN r ON r.lead_id = l.id
        GROUP BY l.source ORDER BY count(*) DESC, l.source"""),
        p,
    ).mappings()
    out = []
    for row in rows:
        d = {k: (int(v) if k != "source" else v) for k, v in row.items()}
        d["email_rate"] = _ratio(d["with_email"], d["leads"])
        d["bounce_rate"] = _ratio(d["bounced"], d["reached"] + d["bounced"])
        d["reply_rate"] = _ratio(d["replied"], d["reached"])
        out.append(d)
    return out


def metrics(conn: Connection, tenant_id: uuid.UUID, start: date, end: date) -> dict[str, Any]:
    """Counts for leads/messages/replies/meetings created in [start, end] (inclusive dates, UTC).

    The business KPIs are per 100 people REACHED (first email delivered: sent and not bounced), the denominator
    that matters to a client. `data_mode` says whether the numbers come from dry-run records or real sends, so a
    demo funnel can never be mistaken for real results."""
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
    sends = conn.execute(
        text("""SELECT count(*) AS sent,
               count(*) FILTER (WHERE status = 'bounced') AS bounced,
               count(*) FILTER (WHERE status = 'complained') AS complained,
               count(*) FILTER (WHERE mode_at_send = 'live') AS live,
               count(*) FILTER (WHERE mode_at_send = 'dry_run') AS dry_run,
               count(DISTINCT lead_id) FILTER (WHERE sequence_step = 0 AND status <> 'bounced') AS reached
        FROM messages WHERE tenant_id = :t AND kind = 'outreach'
        AND status IN ('sent','dry_run_sent','bounced','complained') AND sent_at >= :s AND sent_at < :e"""),
        p,
    ).one()
    sent, bounced, complained, reached = int(sends.sent), int(sends.bounced), int(sends.complained), int(sends.reached)
    data_mode = (
        "none" if not sent else "live" if sends.live == sent else "dry_run" if sends.dry_run == sent else "mixed"
    )
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
    qualified = one("""SELECT count(*) FROM meetings m JOIN qualifications q ON q.lead_id = m.lead_id
        WHERE m.tenant_id = :t AND m.booked_by_system AND m.status = 'attended' AND q.overall_met
        AND m.starts_at >= :s AND m.starts_at < :e""")
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
        "data_mode": data_mode,
        "emails_sent": sent,
        "emails_delivered": sent - bounced,
        "bounces": bounced,
        "complaints": complained,
        "bounce_rate": _ratio(bounced, sent),
        "complaint_rate": _ratio(complained, sent),
        "people_reached": reached,
        "per_100_reached": {
            "replies": _per100(replies, reached),
            "positive_replies": _per100(positive, reached),
            "meetings_booked": _per100(booked, reached),
            "meetings_attended": _per100(attended, reached),
            "qualified_meetings": _per100(qualified, reached),
        },
        "qualified_meetings": qualified,
        "subject_test": subject_test(conn, p),
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
        "by_source": by_source(conn, p),
    }


def roi(conn: Connection, tenant_id: uuid.UUID, m: dict[str, Any]) -> dict[str, Any]:
    """Estimated return for the client from THEIR deal value and close rate (config `roi`), against what they were
    invoiced in the period. An estimate, labelled as such; None until the client gives both numbers."""
    from app.domain.tenants.config import load_config

    try:
        _, cfg = load_config(conn, tenant_id)
    except LookupError:
        return {"status": "not_configured"}
    if cfg.roi is None:
        return {"status": "not_configured"}
    currency = cfg.pricing.currency if cfg.pricing else "GBP"
    pipeline = m["qualified_meetings"] * cfg.roi.average_deal_value_minor
    expected = pipeline * cfg.roi.close_rate_pct // 100
    cost = int(m["billed_minor"])
    return {
        "status": "estimate",
        "currency": currency,
        "average_deal_value_minor": cfg.roi.average_deal_value_minor,
        "close_rate_pct": cfg.roi.close_rate_pct,
        "pipeline_value_minor": pipeline,
        "expected_revenue_minor": expected,
        "invoiced_minor": cost,
        "roi_multiple": round(expected / cost, 1) if cost else None,
    }
