"""Human approval queue (hybrid model). Approval mode 'always' for every new tenant (minimum 2 weeks, enforced in
the database); afterwards 'sampling' sends a configurable share to humans and auto-approves the rest (recorded as
sampled). Every decision and edit is logged and kept as data for improving prompts."""

from __future__ import annotations

import difflib
import hashlib
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.agents.writer.checks import MASK_TOKEN, URL, words
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.domain.leads.state_machine import State, transition

Decision = Literal["approve", "edit", "reject"]


class ApprovalError(Exception):
    pass


@dataclass(frozen=True)
class Edit:
    message_id: uuid.UUID
    body: str
    subject: str | None = None


def in_sample(lead_id: uuid.UUID, pct: int) -> bool:
    """Deterministic sampling: the same lead always gets the same answer (auditable, retry-safe)."""
    return int(hashlib.sha256(lead_id.bytes).hexdigest()[:8], 16) % 100 < pct


def route(conn: Connection, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> str:
    """Called after the writer: either leave for a human, or auto-approve under sampling mode."""
    t = conn.execute(
        text("SELECT approval_mode, approval_sample_pct FROM tenants WHERE id = :t"), {"t": tenant_id}
    ).one()
    if t.approval_mode == "always" or in_sample(lead_id, t.approval_sample_pct):
        return "human"
    msgs = (
        conn.execute(text("SELECT id FROM messages WHERE lead_id = :l AND status = 'pending_approval'"), {"l": lead_id})
        .scalars()
        .all()
    )
    for mid in msgs:
        conn.execute(text("UPDATE messages SET status = 'approved' WHERE id = :m"), {"m": mid})
        conn.execute(
            text("INSERT INTO approvals (tenant_id, message_id, decision, sampled) VALUES (:t, :m, 'approved', true)"),
            {"t": tenant_id, "m": mid},
        )
    transition(
        conn,
        lead_id,
        State.APPROVED,
        actor_type="system",
        actor_id="approval_sampling",
        reason=f"auto-approved (not in {t.approval_sample_pct}% human sample)",
    )
    return "auto"


def decide(
    conn: Connection,
    cipher: PiiCipher,
    *,
    tenant_id: uuid.UUID,
    head_message_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    decision: Decision,
    edits: list[Edit],
    reason: str | None,
    review_seconds: int | None,
) -> dict[str, object]:
    head = (
        conn.execute(
            text("SELECT id, lead_id, status, sequence_step FROM messages WHERE id = :m FOR UPDATE"),
            {"m": head_message_id},
        )
        .mappings()
        .first()
    )
    if head is None:
        raise ApprovalError("message not found")
    if head["sequence_step"] != 0:
        raise ApprovalError("decide on the sequence's first message")
    if head["status"] != "pending_approval":
        raise ApprovalError(f"message is {head['status']}, not pending approval")
    lead_id = head["lead_id"]
    seq = (
        conn.execute(
            text(
                "SELECT id, subject_enc, body_enc FROM messages WHERE lead_id = :l AND status = "
                "'pending_approval' ORDER BY sequence_step FOR UPDATE"
            ),
            {"l": lead_id},
        )
        .mappings()
        .all()
    )
    by_id = {m["id"]: m for m in seq}
    if decision == "reject":
        if not reason or len(reason.strip()) < 3:
            raise ApprovalError("a rejection needs a reason")
        for m in seq:
            conn.execute(text("UPDATE messages SET status = 'cancelled' WHERE id = :m"), {"m": m["id"]})
            conn.execute(
                text("""INSERT INTO approvals (tenant_id, message_id, reviewer_id, decision, reason, review_seconds)
                  VALUES (:t, :m, :r, 'rejected', :why, :s)"""),
                {"t": tenant_id, "m": m["id"], "r": reviewer_id, "why": reason, "s": review_seconds},
            )
        transition(
            conn,
            lead_id,
            State.DISQUALIFIED,
            actor_type="user",
            actor_id=str(reviewer_id),
            reason=f"rejected in review: {reason[:100]}",
        )
        write_audit(
            conn,
            action="approval.rejected",
            actor_type="user",
            actor_id=str(reviewer_id),
            tenant_id=tenant_id,
            entity_type="lead",
            entity_id=lead_id,
            after={"messages": len(seq)},
        )
        return {"status": "rejected", "lead_state": "disqualified"}

    edit_map = {e.message_id: e for e in edits}
    if decision == "edit" and not edits:
        raise ApprovalError("edit decision without edits")
    unknown = set(edit_map) - set(by_id)
    if unknown:
        raise ApprovalError("edits reference messages outside this sequence")
    warnings: list[str] = []
    for m in seq:
        e = edit_map.get(m["id"])
        if e is None:
            conn.execute(text("UPDATE messages SET status = 'approved' WHERE id = :m"), {"m": m["id"]})
            conn.execute(
                text("""INSERT INTO approvals (tenant_id, message_id, reviewer_id, decision, review_seconds)
                  VALUES (:t, :m, :r, 'approved', :s)"""),
                {"t": tenant_id, "m": m["id"], "r": reviewer_id, "s": review_seconds},
            )
            continue
        if MASK_TOKEN.search(e.body) or (e.subject and MASK_TOKEN.search(e.subject)):
            raise ApprovalError("edit contains masked tokens (e.g. 'A***'); unmask before editing")
        if URL.search(e.body):
            raise ApprovalError("links are not allowed in the message body (plain-text-first policy)")
        if words(e.body) > 120:
            raise ApprovalError("edited message is longer than 120 words")
        if "unsubscribe" in e.body.lower():
            raise ApprovalError("do not add footer text; the compliance footer is appended automatically")
        old_body = cipher.decrypt(conn, tenant_id, "messages.body", m["body_enc"])
        old_subject = cipher.decrypt(conn, tenant_id, "messages.subject", m["subject_enc"])
        diff = "\n".join(
            difflib.unified_diff(old_body.splitlines(), e.body.splitlines(), "draft", "approved", lineterm="")
        )
        conn.execute(
            text("UPDATE messages SET body_enc = :b, subject_enc = :s, status = 'approved' WHERE id = :m"),
            {
                "b": cipher.encrypt(conn, tenant_id, "messages.body", e.body),
                "s": cipher.encrypt(conn, tenant_id, "messages.subject", e.subject or old_subject),
                "m": m["id"],
            },
        )
        conn.execute(
            text("""INSERT INTO approvals (tenant_id, message_id, reviewer_id, decision, original_body_enc,
              edited_body_enc, original_subject_enc, edited_subject_enc, diff, reason, review_seconds)
              VALUES (:t, :m, :r, 'edited', :ob, :eb, :os, :es, :d, :why, :sec)"""),
            {
                "t": tenant_id,
                "m": m["id"],
                "r": reviewer_id,
                "ob": m["body_enc"],
                "eb": cipher.encrypt(conn, tenant_id, "approvals.edited_body", e.body),
                "os": m["subject_enc"],
                "es": cipher.encrypt(conn, tenant_id, "approvals.edited_subject", e.subject) if e.subject else None,
                "d": cipher.encrypt(conn, tenant_id, "approvals.diff", diff).hex(),
                "why": reason,
                "sec": review_seconds,
            },
        )
        # claims that no longer appear in the edited text are no longer personalisation claims
        conn.execute(
            text(
                "DELETE FROM message_personalization_sources WHERE message_id = :m AND position(lower(claim_text) "
                "in lower(:b)) = 0"
            ),
            {"m": m["id"], "b": e.body},
        )
        warnings.append(f"message {m['id']} edited")
    transition(
        conn,
        lead_id,
        State.APPROVED,
        actor_type="user",
        actor_id=str(reviewer_id),
        reason="approved with edits" if edits else "approved",
    )
    write_audit(
        conn,
        action="approval.approved",
        actor_type="user",
        actor_id=str(reviewer_id),
        tenant_id=tenant_id,
        entity_type="lead",
        entity_id=lead_id,
        after={"edited": len(edits), "messages": len(seq)},
    )
    return {"status": "approved", "lead_state": "approved", "edited": len(edits), "notes": warnings}
