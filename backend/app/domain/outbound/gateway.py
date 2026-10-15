"""The outbound gateway — the ONLY code path that can cause an external side effect (ADR-0004).

send_message():
  tx1  lock message -> fresh compliance decision (stage=send) incl. suppression -> deliverability gates
       (inbox, business hours, caps) -> resolve mode -> mark 'sending' (at-most-once) -> commit
  call dry-run recorder (default) or the live/sandbox sender
  tx2  mark 'sent' / 'dry_run_sent' (the DB send-gate trigger re-verifies) -> lead -> contacted -> commit
A crash between tx1 and tx2 leaves the message in 'sending'; the reconciler moves it to
'needs_reconciliation' for a human. It is NEVER automatically re-sent.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import RowMapping, text
from sqlalchemy.engine import Connection, Engine

from app.adapters.base import RetryableProviderError
from app.adapters.email.base import EmailPayload, EmailSender, SendResult
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.core.logging import get_logger
from app.db.session import DbContext, transaction
from app.domain.compliance.engine import ComplianceDecision, ProspectFacts, evaluate
from app.domain.compliance.repository import build_context, record_decision
from app.domain.deliverability.hours import UnknownJurisdiction, is_business_time, next_business_start
from app.domain.leads.state_machine import State, TransitionError, get_lead, transition
from app.domain.outbound import dry_run
from app.domain.outbound.footer import FooterError, finalise_body
from app.domain.outbound.modes import SendMode, resolve_mode
from app.domain.suppression import unsubscribe
from app.domain.suppression.service import RecipientHashes
from app.domain.suppression.service import check as check_suppression
from app.domain.tenants.controls import contacts_used
from app.settings import Settings

log = get_logger(__name__)

SendStatus = Literal["sent", "dry_run_sent", "blocked", "deferred", "skipped", "retry_later"]
SENDABLE = ("approved", "queued", "failed_retryable")


@dataclass(frozen=True)
class SendOutcome:
    status: SendStatus
    reason_codes: tuple[str, ...] = ()
    decision_id: uuid.UUID | None = None
    scheduled_for: datetime | None = None
    mode: SendMode | None = None
    provider_message_id: str | None = None


@dataclass
class _Prepared:
    payload: EmailPayload
    mode: SendMode
    decision_id: uuid.UUID
    lead_id: uuid.UUID


@dataclass
class OutboundGateway:
    settings: Settings
    engine: Engine
    cipher: PiiCipher
    live_sender: EmailSender | None = None
    sandbox_sender: EmailSender | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ public API
    def send_message(self, tenant_id: uuid.UUID, message_id: uuid.UUID) -> SendOutcome:
        ctx = DbContext.system(tenant_id)
        with transaction(ctx, self.engine) as conn:
            prepared = self._prepare(conn, tenant_id, message_id)
        if isinstance(prepared, SendOutcome):
            return prepared

        try:
            result = self._dispatch(tenant_id, message_id, prepared)
        except RetryableProviderError as exc:
            with transaction(ctx, self.engine) as conn:
                conn.execute(
                    text(
                        "UPDATE messages SET status = 'failed_retryable', last_error = :e WHERE id = :id "
                        "AND status = 'sending'"
                    ),
                    {"e": type(exc).__name__, "id": message_id},
                )
            return SendOutcome("retry_later", (type(exc).__name__,), prepared.decision_id, mode=prepared.mode)
        # Any other exception propagates and leaves the message in 'sending' -> reconciliation (never resent).

        with transaction(ctx, self.engine) as conn:
            final: Literal["sent", "dry_run_sent"] = "dry_run_sent" if prepared.mode == "dry_run" else "sent"
            conn.execute(
                text("UPDATE messages SET status = :s, sent_at = now(), provider_message_id = :p WHERE id = :id"),
                {"s": final, "p": result.provider_message_id, "id": message_id},
            )
            lead = get_lead(conn, prepared.lead_id, for_update=True)
            if lead.state == State.APPROVED:
                transition(
                    conn,
                    prepared.lead_id,
                    State.CONTACTED,
                    actor_type="system",
                    actor_id="outbound_gateway",
                    reason=f"message {message_id} {final}",
                )
            write_audit(
                conn,
                action=f"message.{final}",
                actor_type="system",
                actor_id="outbound_gateway",
                tenant_id=tenant_id,
                entity_type="message",
                entity_id=message_id,
                after={"mode": prepared.mode, "decision_id": str(prepared.decision_id)},
            )
        log.info("message_sent", message_id=str(message_id), mode=prepared.mode, status=final)
        return SendOutcome(
            final,
            ("OK",),
            prepared.decision_id,
            mode=prepared.mode,
            provider_message_id=result.provider_message_id,
        )

    # ------------------------------------------------------------------ steps
    def _prepare(self, conn: Connection, tenant_id: uuid.UUID, message_id: uuid.UUID) -> _Prepared | SendOutcome:
        m = (
            conn.execute(text("SELECT * FROM messages WHERE id = :id FOR UPDATE"), {"id": message_id})
            .mappings()
            .first()
        )
        if m is None:
            return SendOutcome("skipped", ("MESSAGE_NOT_FOUND",))
        if m["status"] in ("sent", "dry_run_sent", "sending", "needs_reconciliation"):
            return SendOutcome("skipped", (f"ALREADY_{m['status'].upper()}",))
        if m["status"] not in SENDABLE:
            return SendOutcome("skipped", (f"NOT_SENDABLE_{m['status'].upper()}",))

        row = (
            conn.execute(
                text("""
            SELECT l.id AS lead_id, l.state, l.campaign_id, p.email_enc, p.country_code AS p_country,
                   p.subdivision_code, p.subscriber_type, p.email_verification, p.role_relevance,
                   c.country_code AS c_country, t.live_mode, t.legal_name, t.slug,
                   i.address AS inbox_address, i.display_name, i.status AS inbox_status, i.daily_cap AS inbox_cap,
                   i.oauth_status, i.provider AS inbox_provider, cp.daily_cap AS campaign_cap,
                   t.status AS tenant_status, cp.status AS campaign_status, cp.pilot_max_contacts
            FROM leads l
            JOIN prospects p ON p.id = l.prospect_id
            JOIN companies c ON c.id = l.company_id
            JOIN tenants t ON t.id = l.tenant_id
            JOIN campaigns cp ON cp.id = l.campaign_id
            LEFT JOIN inboxes i ON i.id = :inbox
            WHERE l.id = :lead"""),
                {"lead": m["lead_id"], "inbox": m["inbox_id"]},
            )
            .mappings()
            .one()
        )

        if m["kind"] == "outreach" and m["sequence_step"] > 0 and row["state"] != "contacted":
            # Follow-ups only while the lead is still 'contacted' (no reply, objection, booking or suppression).
            conn.execute(
                text("UPDATE messages SET status = 'cancelled', last_error = :e WHERE id = :id"),
                {"e": f"lead is {row['state']}", "id": message_id},
            )
            return SendOutcome("skipped", (f"FOLLOW_UP_CANCELLED_LEAD_{str(row['state']).upper()}",))
        # 0) emergency stop and pilot limit: HOLD (stay queued), never reject. Checked before compliance so a
        # paused client never reads as a compliance rejection, and resuming sends exactly what was waiting.
        if row["tenant_status"] == "paused":
            return self._block(conn, tenant_id, m, None, ("CLIENT_PAUSED",), permanent=False)
        if row["campaign_status"] in ("paused", "auto_paused"):
            return self._block(conn, tenant_id, m, None, ("CAMPAIGN_PAUSED",), permanent=False)
        if (
            m["kind"] == "outreach"
            and m["sequence_step"] == 0
            and row["pilot_max_contacts"] is not None
            and self._pilot_full(conn, row["campaign_id"], row["pilot_max_contacts"])
        ):
            return self._block(conn, tenant_id, m, None, ("PILOT_LIMIT_REACHED",), permanent=False)
        if row["email_enc"] is None:
            return self._block(conn, tenant_id, m, None, ("PROSPECT_HAS_NO_EMAIL",))
        email = self.cipher.decrypt(conn, tenant_id, "prospects.email", row["email_enc"])
        hashes = RecipientHashes.for_email(self.settings.key("suppression_hash_key"), email)
        if hashes.email != bytes(m["recipient_hash"]) or hashes.domain != bytes(m["recipient_domain_hash"]):
            return self._block(conn, tenant_id, m, None, ("RECIPIENT_MISMATCH",))

        # 1) fresh compliance decision, including suppression, at send time
        suppression = check_suppression(conn, tenant_id, hashes)
        facts = ProspectFacts(
            row["p_country"],
            row["c_country"],
            row["subscriber_type"],
            row["email_verification"],
            float(row["role_relevance"]) if row["role_relevance"] is not None else None,
        )
        context = build_context(
            conn,
            stage="send",
            tenant_id=tenant_id,
            campaign_id=row["campaign_id"],
            prospect=facts,
            suppression=suppression,
        )
        decision = evaluate(context)
        decision_id = record_decision(
            conn, tenant_id=tenant_id, lead_id=m["lead_id"], stage="send", decision=decision, message_id=message_id
        )
        if decision.outcome != "allow":
            return self._block(conn, tenant_id, m, decision, tuple(r.value for r in decision.reason_codes), decision_id)

        # 2) deliverability gates
        if row["inbox_address"] is None or row["inbox_status"] == "paused":
            return self._block(conn, tenant_id, m, None, ("INBOX_UNAVAILABLE",), decision_id, permanent=False)
        now = self.clock()
        try:
            in_hours = is_business_time(now, row["p_country"], row["subdivision_code"])
        except UnknownJurisdiction:
            return self._block(conn, tenant_id, m, None, ("TIMEZONE_UNKNOWN",), decision_id)
        if not in_hours:
            return self._defer(
                conn,
                m,
                decision_id,
                next_business_start(now, row["p_country"], row["subdivision_code"]),
                "OUTSIDE_BUSINESS_HOURS",
            )
        if self._sent_today(conn, "inbox_id", m["inbox_id"], now) >= row["inbox_cap"]:
            return self._defer(
                conn, m, decision_id, self._tomorrow(now, row["p_country"], row["subdivision_code"]), "INBOX_DAILY_CAP"
            )
        if self._sent_today(conn, "campaign_id", m["campaign_id"], now) >= row["campaign_cap"]:
            return self._defer(
                conn,
                m,
                decision_id,
                self._tomorrow(now, row["p_country"], row["subdivision_code"]),
                "CAMPAIGN_DAILY_CAP",
            )

        # 3) mode (three-key switch)
        golive = bool(conn.execute(text("SELECT golive_complete('uk')")).scalar())
        mode = resolve_mode(
            adapter_mode=self.settings.email_adapter_mode,
            tenant_live=row["live_mode"],
            global_go_live=self.settings.global_go_live,
            golive_complete=golive,
        )
        if mode != "dry_run" and row["oauth_status"] != "ok":
            return self._block(conn, tenant_id, m, None, ("INBOX_OAUTH_INVALID",), decision_id, permanent=False)

        # 4) render final message with signed one-click unsubscribe
        token_id = conn.execute(
            text("""INSERT INTO unsubscribe_tokens (tenant_id, message_id, lead_id, recipient_hash,
              recipient_domain_hash) VALUES (:t, :m, :l, :rh, :dh) RETURNING id"""),
            {"t": tenant_id, "m": message_id, "l": m["lead_id"], "rh": hashes.email, "dh": hashes.domain},
        ).scalar_one()
        unsub_url = (
            f"{self.settings.api_base_url}/u/{unsubscribe.sign(self.settings.key('unsubscribe_signing_key'), token_id)}"
        )
        privacy_url = f"{self.settings.public_base_url}/privacy/{row['slug']}"
        subject = self.cipher.decrypt(conn, tenant_id, "messages.subject", m["subject_enc"]) if m["subject_enc"] else ""
        body = self.cipher.decrypt(conn, tenant_id, "messages.body", m["body_enc"]) if m["body_enc"] else ""
        try:
            final_body = finalise_body(body, m["compliance_footer"] or "", unsub_url, privacy_url)
        except FooterError:
            return self._block(conn, tenant_id, m, None, ("FOOTER_INVALID",), decision_id)
        payload = EmailPayload(
            to_email=email,
            from_address=row["inbox_address"],
            from_name=row["display_name"],
            subject=subject,
            body_text=final_body,
            idempotency_key=m["idempotency_key"],
            headers={"List-Unsubscribe": f"<{unsub_url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
            provider=row["inbox_provider"] or "mock",
        )

        # 5) at-most-once: mark 'sending' and commit BEFORE calling any provider
        conn.execute(
            text("""UPDATE messages SET status = 'sending', sending_started_at = :now, mode_at_send = :mode,
              send_compliance_decision_id = :d, suppression_checked_at = :sc WHERE id = :id"""),
            {"now": now, "mode": mode, "d": decision_id, "sc": suppression.checked_at, "id": message_id},
        )
        return _Prepared(payload, mode, decision_id, m["lead_id"])

    def _dispatch(self, tenant_id: uuid.UUID, message_id: uuid.UUID, p: _Prepared) -> SendResult:
        if p.mode == "dry_run":
            with transaction(DbContext.system(tenant_id), self.engine) as conn:
                return dry_run.record_email(
                    conn, self.cipher, self.settings.key("blind_index_key"), tenant_id, message_id, p.payload
                )
        sender = self.live_sender if p.mode == "live" else self.sandbox_sender
        if sender is None:
            raise RuntimeError(f"no {p.mode} email sender configured")
        return sender.send(p.payload)

    # ------------------------------------------------------------------ helpers
    def _block(
        self,
        conn: Connection,
        tenant_id: uuid.UUID,
        m: RowMapping,
        decision: ComplianceDecision | None,
        reasons: tuple[str, ...],
        decision_id: uuid.UUID | None = None,
        *,
        permanent: bool = True,
    ) -> SendOutcome:
        message_id: uuid.UUID = m["id"]
        lead_id: uuid.UUID = m["lead_id"]
        status = "blocked_at_send" if permanent else "queued"
        conn.execute(
            text("UPDATE messages SET status = :s, last_error = :e WHERE id = :id"),
            {"s": status, "e": ",".join(reasons), "id": message_id},
        )
        if decision is not None:
            target = (
                State.SUPPRESSED
                if decision.suppressed
                else State.ALTERNATIVE_CHANNEL
                if decision.outcome == "alternative_channel"
                else State.REJECTED_BY_COMPLIANCE
            )
            try:
                transition(
                    conn,
                    lead_id,
                    target,
                    actor_type="system",
                    actor_id="outbound_gateway",
                    reason="send-time compliance: " + ",".join(reasons),
                )
            except TransitionError:
                log.warning("blocked_lead_transition_skipped", lead_id=str(lead_id))
        write_audit(
            conn,
            action="message.blocked_at_send" if permanent else "message.held",
            actor_type="system",
            actor_id="outbound_gateway",
            tenant_id=tenant_id,
            entity_type="message",
            entity_id=message_id,
            after={"reasons": list(reasons), "decision_id": str(decision_id) if decision_id else None},
        )
        return SendOutcome("blocked", reasons, decision_id)

    def _defer(
        self, conn: Connection, m: RowMapping, decision_id: uuid.UUID, when: datetime, reason: str
    ) -> SendOutcome:
        conn.execute(
            text("UPDATE messages SET status = 'queued', scheduled_for = :w WHERE id = :id"), {"w": when, "id": m["id"]}
        )
        return SendOutcome("deferred", (reason,), decision_id, scheduled_for=when)

    @staticmethod
    def _pilot_full(conn: Connection, campaign_id: uuid.UUID, limit: int) -> bool:
        # Lock the campaign row so two concurrent first emails cannot both take the last pilot slot.
        conn.execute(text("SELECT 1 FROM campaigns WHERE id = :c FOR UPDATE"), {"c": campaign_id})
        return contacts_used(conn, campaign_id) >= limit

    @staticmethod
    def _sent_today(conn: Connection, column: Literal["inbox_id", "campaign_id"], value: object, now: datetime) -> int:
        start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        sql = (
            f"SELECT count(*) FROM messages WHERE {column} = :v AND status IN "  # noqa: S608 (column is a Literal)
            "('sending','sent','dry_run_sent','needs_reconciliation') AND sending_started_at >= :s"
        )
        return int(conn.execute(text(sql), {"v": value, "s": start}).scalar_one())

    @staticmethod
    def _tomorrow(now: datetime, country: str, subdivision: str | None) -> datetime:
        midnight = (now.astimezone(UTC) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return next_business_start(midnight, country, subdivision)


def reconcile_stuck_sends(engine: Engine, tenant_id: uuid.UUID, older_than_minutes: int) -> int:
    """Messages stuck in 'sending' -> 'needs_reconciliation' + escalation. Never re-sent automatically."""
    with transaction(DbContext.system(tenant_id), engine) as conn:
        rows = conn.execute(
            text("""UPDATE messages SET status = 'needs_reconciliation'
              WHERE status = 'sending' AND sending_started_at < now() - make_interval(mins => :m)
              RETURNING id, lead_id"""),
            {"m": older_than_minutes},
        ).all()
        for r in rows:
            conn.execute(
                text("""INSERT INTO escalations (tenant_id, lead_id, reason, context)
                  VALUES (:t, :l, 'needs_reconciliation', CAST(:c AS jsonb))"""),
                {"t": tenant_id, "l": r.lead_id, "c": f'{{"message_id": "{r.id}"}}'},
            )
            write_audit(
                conn,
                action="message.needs_reconciliation",
                actor_type="system",
                actor_id="reconciler",
                tenant_id=tenant_id,
                entity_type="message",
                entity_id=r.id,
            )
        return len(rows)
