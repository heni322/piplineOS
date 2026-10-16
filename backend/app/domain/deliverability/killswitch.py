"""Deliverability kill switch: pauses an inbox whose live bounce or complaint rate crosses the configured limits,
and records hard bounces (suppressing the address). Only LIVE sends count: dry-run and sandbox never reach a
real inbox, so they say nothing about reputation."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.adapters.inbound.base import InboundEmail
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.domain.leads.state_machine import TERMINAL, State, get_lead, transition
from app.domain.suppression.service import RecipientHashes, suppress_email


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_days: int = Field(default=7, ge=1, le=60)
    min_sent: int = Field(default=50, ge=1)
    max_hard_bounce_rate: float = Field(default=0.03, gt=0, lt=1)
    max_complaint_rate: float = Field(default=0.003, gt=0, lt=1)
    warn_complaint_rate: float = Field(default=0.001, gt=0, lt=1)

    @classmethod
    def load(cls, root: Path) -> Thresholds:
        path = root / "deliverability.yaml"
        return cls.model_validate(yaml.safe_load(path.read_text())) if path.exists() else cls()


@dataclass(frozen=True)
class Assessment:
    pause: bool
    warn: bool
    reasons: tuple[str, ...]


def assess(sent: int, hard_bounced: int, complained: int, th: Thresholds) -> Assessment:
    """Pure. Below the minimum volume we never pause (a single bounce on 3 sends isn't a signal)."""
    if sent < th.min_sent:
        return Assessment(False, False, ())
    reasons = []
    if hard_bounced / sent >= th.max_hard_bounce_rate:
        reasons.append("HARD_BOUNCE_RATE")
    if complained / sent >= th.max_complaint_rate:
        reasons.append("COMPLAINT_RATE")
    warn = not reasons and complained / sent >= th.warn_complaint_rate
    return Assessment(bool(reasons), warn, tuple(reasons))


def evaluate(conn: Connection, tenant_id: uuid.UUID, now: datetime, th: Thresholds) -> list[dict[str, object]]:
    rows = conn.execute(
        text("""SELECT i.id, i.status,
                 count(*) FILTER (WHERE m.mode_at_send = 'live'
                                  AND m.status IN ('sent','bounced','complained')) AS sent,
                 count(*) FILTER (WHERE m.mode_at_send = 'live' AND m.status = 'bounced'
                                  AND m.last_error LIKE 'hard_bounce%') AS hard,
                 count(*) FILTER (WHERE m.mode_at_send = 'live' AND m.status = 'complained') AS complained
          FROM inboxes i LEFT JOIN messages m ON m.inbox_id = i.id AND m.sent_at >= :since
          WHERE i.tenant_id = :t GROUP BY i.id, i.status"""),
        {"t": tenant_id, "since": now - timedelta(days=th.window_days)},
    ).all()
    out = []
    for r in rows:
        a = assess(int(r.sent), int(r.hard), int(r.complained), th)
        if a.pause and r.status != "paused":
            conn.execute(text("UPDATE inboxes SET status = 'paused' WHERE id = :i"), {"i": r.id})
            write_audit(
                conn,
                action="deliverability.inbox_paused",
                actor_type="system",
                actor_id="kill_switch",
                tenant_id=tenant_id,
                entity_type="inbox",
                entity_id=r.id,
                after={
                    "reasons": list(a.reasons),
                    "sent": int(r.sent),
                    "hard": int(r.hard),
                    "complained": int(r.complained),
                },
            )
        out.append(
            {
                "inbox_id": str(r.id),
                "paused": a.pause or r.status == "paused",
                "warn": a.warn,
                "reasons": list(a.reasons),
                "sent": int(r.sent),
            }
        )
    return out


# --------------------------------------------------------------------------------------------- bounces
_FROM = re.compile(r"^(mailer-daemon|postmaster|microsoftexchange[0-9a-f]*)@", re.IGNORECASE)
_SUBJECT = re.compile(
    r"undeliverable|delivery status notification|mail delivery (failed|subsystem)|"
    r"returned mail|failure notice|delivery has failed",
    re.IGNORECASE,
)
# RFC 3463 enhanced status codes. Only 5.1.x (bad address/mailbox) and 5.2.1 (mailbox disabled) say THE RECIPIENT
# is unreachable for good. Others are about US or the moment: 5.7.x (policy, reputation, authentication), 5.4.x
# (routing), 5.3.x (their system), 5.2.2 (mailbox full). Those must never suppress the person everywhere.
_CODE = re.compile(r"\b([45])\.(\d{1,3})\.(\d{1,3})\b")
_HARD_PHRASE = re.compile(
    r"does not exist|user unknown|no such user|recipient address rejected|"
    r"address couldn't be found|mailbox unavailable|invalid recipient",
    re.IGNORECASE,
)
BounceKind = Literal["hard", "policy", "soft"]


def classify_bounce(body: str) -> BounceKind:
    """hard: the address is dead (suppress it). policy: a permanent refusal about the sender (counts against the
    sending inbox, the person is NOT suppressed). soft: temporary (logged only)."""
    codes = [(int(a), int(b), int(c)) for a, b, c in _CODE.findall(body)]
    if any(a == 5 and (b == 1 or (b, c) == (2, 1)) for a, b, c in codes):
        return "hard"
    if any(a == 5 for a, _, _ in codes):
        return "policy"
    if codes:  # only 4.x.x codes: temporary
        return "soft"
    return "hard" if _HARD_PHRASE.search(body) else "soft"


_MSGID = re.compile(r"<[^<>@\s]{8,}@[^<>\s]+>")


def is_bounce(msg: InboundEmail) -> bool:
    return bool(_FROM.match(msg.from_email) or _SUBJECT.search(msg.subject or ""))


def handle_bounce(
    conn: Connection, settings_key: bytes, cipher: PiiCipher, tenant_id: uuid.UUID, msg: InboundEmail
) -> list[str]:
    """Matches the bounce to the message we sent (In-Reply-To, else a Message-ID quoted in the body). Hard bounce:
    message -> bounced, address suppressed, lead -> suppressed. Soft bounce: logged only."""
    candidates = [msg.in_reply_to] if msg.in_reply_to else []
    candidates += _MSGID.findall(msg.body_text or "")[:20]
    m = None
    for c in candidates:
        m = conn.execute(
            text("""SELECT m.id, m.lead_id, m.status, p.email_enc FROM messages m JOIN leads l ON l.id = m.lead_id
              JOIN prospects p ON p.id = l.prospect_id WHERE m.provider_message_id = :p"""),
            {"p": c},
        ).first()
        if m:
            break
    if m is None:
        write_audit(
            conn, action="deliverability.bounce_unmatched", actor_type="system", actor_id="inbound", tenant_id=tenant_id
        )
        return ["bounce:unmatched"]
    kind = classify_bounce(msg.body_text or "")
    if kind == "policy":
        # The receiving server refused US (reputation, authentication, policy). Counted as a bounce for this inbox,
        # so the kill switch can pause it; the recipient is not suppressed and their lead is left as it is.
        if m.status == "sent":
            conn.execute(
                text("UPDATE messages SET status = 'bounced', last_error = 'policy_block' WHERE id = :i"), {"i": m.id}
            )
        conn.execute(  # their server will refuse the follow-ups too: stop them (the person stays contactable)
            text(
                "UPDATE messages SET status = 'cancelled' WHERE lead_id = :l AND status IN "
                "('draft','pending_approval','approved','queued','failed_retryable')"
            ),
            {"l": m.lead_id},
        )
        write_audit(
            conn,
            action="deliverability.policy_block",
            actor_type="system",
            actor_id="inbound",
            tenant_id=tenant_id,
            entity_type="message",
            entity_id=m.id,
        )
        return ["bounce:policy"]
    if kind == "soft":
        write_audit(
            conn,
            action="deliverability.soft_bounce",
            actor_type="system",
            actor_id="inbound",
            tenant_id=tenant_id,
            entity_type="message",
            entity_id=m.id,
        )
        return ["bounce:soft"]
    if m.status == "sent":
        conn.execute(
            text("UPDATE messages SET status = 'bounced', last_error = 'hard_bounce' WHERE id = :i"), {"i": m.id}
        )
    if m.email_enc is not None:
        email = cipher.decrypt(conn, tenant_id, "prospects.email", m.email_enc)
        suppress_email(conn, RecipientHashes.for_email(settings_key, email), "bounce_hard", source_ref=f"bounce:{m.id}")
    lead = get_lead(conn, m.lead_id)
    if lead.state not in TERMINAL:
        transition(conn, m.lead_id, State.SUPPRESSED, actor_type="system", actor_id="inbound", reason="hard bounce")
    conn.execute(
        text(
            "UPDATE messages SET status = 'cancelled' WHERE lead_id = :l AND status IN "
            "('draft','pending_approval','approved','queued','failed_retryable')"
        ),
        {"l": m.lead_id},
    )
    return ["bounce:hard", "suppressed"]
