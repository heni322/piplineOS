"""Inbox placement checks (go-live runbook C2). PipelineOS sends only to real leads through the outbound gateway, so
placement is tested by hand with free tools, then recorded here. This module decides pass/fail by the runbook's
rule and reads the latest result per inbox; it sends nothing.

Pass: score 9/10 or better (when a checker score was taken), SPF, DKIM and DMARC all pass, no blacklist hit, and
the email landed in the inbox (not spam or promotions) on every mailbox tried, with at least one tried.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.audit import write_audit

Placement = Literal["inbox", "spam", "promotions", "missing"]
MIN_SCORE = 9.0
VALID_FOR = timedelta(days=30)  # re-check monthly, and for every new campaign


@dataclass(frozen=True)
class PlacementCheck:
    score: float | None
    spf_pass: bool
    dkim_pass: bool
    dmarc_pass: bool
    blacklisted: bool
    provider_results: dict[str, Placement]  # e.g. {"gmail": "inbox", "outlook.com": "spam"}
    evidence_url: str | None = None


def evaluate(c: PlacementCheck) -> tuple[bool, list[str]]:
    reasons = []
    if c.score is not None and c.score < MIN_SCORE:
        reasons.append(f"checker score {c.score:g}/10 is below {MIN_SCORE:g}")
    for name, ok in (("SPF", c.spf_pass), ("DKIM", c.dkim_pass), ("DMARC", c.dmarc_pass)):
        if not ok:
            reasons.append(f"{name} does not pass")
    if c.blacklisted:
        reasons.append("the sending domain or IP is on a blacklist")
    if not c.provider_results:
        reasons.append("no mailbox was tried (send it to two or three of your own mailboxes)")
    reasons += [f"{p}: landed in {where}" for p, where in sorted(c.provider_results.items()) if where != "inbox"]
    return not reasons, reasons


def record(
    conn: Connection, *, tenant_id: uuid.UUID, inbox_id: uuid.UUID, check: PlacementCheck, operator_id: uuid.UUID
) -> tuple[bool, list[str]]:
    passed, reasons = evaluate(check)
    conn.execute(
        text("""INSERT INTO inbox_placement_checks (tenant_id, inbox_id, score, spf_pass, dkim_pass, dmarc_pass,
                  blacklisted, provider_results, evidence_url, passed, reasons, recorded_by)
                VALUES (:t, :i, :s, :spf, :dkim, :dmarc, :bl, CAST(:pr AS jsonb), :ev, :p, CAST(:r AS jsonb), :u)"""),
        {
            "t": tenant_id,
            "i": inbox_id,
            "s": check.score,
            "spf": check.spf_pass,
            "dkim": check.dkim_pass,
            "dmarc": check.dmarc_pass,
            "bl": check.blacklisted,
            "pr": json.dumps(check.provider_results),
            "ev": check.evidence_url,
            "p": passed,
            "r": json.dumps(reasons),
            "u": operator_id,
        },
    )
    write_audit(
        conn,
        action="deliverability.placement_checked",
        actor_type="user",
        actor_id=str(operator_id),
        tenant_id=tenant_id,
        entity_type="inbox",
        entity_id=inbox_id,
        after={"passed": passed, "reasons": reasons},
    )
    return passed, reasons


@dataclass(frozen=True)
class Latest:
    checked_at: datetime
    passed: bool
    reasons: list[str]

    def current(self, now: datetime) -> bool:
        return self.passed and now - self.checked_at <= VALID_FOR


def latest_by_inbox(conn: Connection) -> dict[uuid.UUID, Latest]:
    rows = conn.execute(
        text("""SELECT DISTINCT ON (inbox_id) inbox_id, created_at, passed, reasons
                FROM inbox_placement_checks ORDER BY inbox_id, created_at DESC""")
    ).all()
    return {r.inbox_id: Latest(r.created_at, r.passed, list(r.reasons or [])) for r in rows}
