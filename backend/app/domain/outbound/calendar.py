"""Calendar side effects go through the outbound layer (same rules as email): availability is read via the
adapter; creating/cancelling events re-checks compliance + suppression and is dry-run unless all three live keys
are on. Dry-run records the full would-be event (incl. an ICS invite) in dry_run_log."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.adapters.calendar.base import CalendarAdapter, Slot
from app.adapters.calendar.mock import MockCalendar
from app.core.audit import write_audit
from app.core.crypto import PiiCipher, blind_index
from app.domain.outbound.checks import recheck_lead
from app.domain.outbound.modes import SendMode, resolve_mode
from app.settings import Settings


class BookingBlocked(Exception):
    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__(", ".join(reasons))
        self.reasons = reasons


def ics(uid: str, slot: Slot, title: str, organiser: str, attendee: str, description: str) -> str:
    fmt = "%Y%m%dT%H%M%SZ"
    desc = description.replace("\n", "\\n")
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//PipelineOS//EN",
            "METHOD:REQUEST",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{datetime.now(UTC).strftime(fmt)}",
            f"DTSTART:{slot.starts_at.astimezone(UTC).strftime(fmt)}",
            f"DTEND:{slot.ends_at.astimezone(UTC).strftime(fmt)}",
            f"SUMMARY:{title}",
            f"DESCRIPTION:{desc}",
            f"ORGANIZER:mailto:{organiser}",
            f"ATTENDEE;RSVP=TRUE:mailto:{attendee}",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )


@dataclass
class CalendarGateway:
    settings: Settings
    cipher: PiiCipher
    adapter: CalendarAdapter = field(default_factory=MockCalendar)  # reads + non-live creates are mock-safe
    live_adapter: CalendarAdapter | None = None

    def busy(self, calendar_ref: str, start: datetime, end: datetime) -> list[Slot]:
        return self.adapter.busy(calendar_ref, start, end)

    def _mode(self, conn: Connection, tenant_id: uuid.UUID) -> SendMode:
        live = bool(conn.execute(text("SELECT live_mode FROM tenants WHERE id = :t"), {"t": tenant_id}).scalar())
        golive = bool(conn.execute(text("SELECT golive_complete('uk')")).scalar())
        return resolve_mode(
            adapter_mode=self.settings.calendar_adapter_mode,
            tenant_live=live,
            global_go_live=self.settings.global_go_live,
            golive_complete=golive,
        )

    def create_event(
        self,
        conn: Connection,
        *,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
        meeting_id: uuid.UUID,
        calendar_ref: str,
        slot: Slot,
        title: str,
        organiser: str,
        description: str,
        idempotency_key: str,
    ) -> tuple[str, SendMode]:
        _, decision, email = recheck_lead(
            conn, self.cipher, self.settings.key("suppression_hash_key"), tenant_id, lead_id
        )
        if decision.outcome != "allow":
            raise BookingBlocked(tuple(r.value for r in decision.reason_codes))
        mode = self._mode(conn, tenant_id)
        if mode == "live":
            if self.live_adapter is None:
                raise RuntimeError("no live calendar adapter configured")
            return self.live_adapter.create_event(calendar_ref, slot, email, title, idempotency_key), mode
        invite = ics(f"{meeting_id}@pipelineos", slot, title, organiser, email, description)
        self._record(
            conn,
            tenant_id,
            "calendar_create",
            idempotency_key,
            meeting_id,
            email,
            {
                "attendee": email,
                "starts_at": slot.starts_at.isoformat(),
                "ends_at": slot.ends_at.isoformat(),
                "title": title,
                "ics": invite,
            },
        )
        # keep the mock calendar consistent so a booked slot shows as busy
        self.adapter.create_event(calendar_ref, slot, email, title, idempotency_key)
        return f"dryrun-evt-{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)}", "dry_run"

    def cancel_event(
        self,
        conn: Connection,
        *,
        tenant_id: uuid.UUID,
        meeting_id: uuid.UUID,
        calendar_ref: str,
        provider_event_id: str,
        idempotency_key: str,
    ) -> None:
        mode = self._mode(conn, tenant_id)
        if mode == "live" and self.live_adapter is not None:
            self.live_adapter.cancel_event(calendar_ref, provider_event_id, idempotency_key)
            return
        self._record(
            conn,
            tenant_id,
            "calendar_cancel",
            idempotency_key,
            meeting_id,
            None,
            {"provider_event_id": provider_event_id},
        )
        self.adapter.cancel_event(calendar_ref, provider_event_id, idempotency_key)

    def _record(
        self,
        conn: Connection,
        tenant_id: uuid.UUID,
        effect: str,
        key: str,
        entity: uuid.UUID,
        email: str | None,
        payload: dict[str, object],
    ) -> None:
        conn.execute(
            text("""INSERT INTO dry_run_log (tenant_id, effect_type, adapter, idempotency_key, entity_id,
              payload_enc, recipient_bidx) VALUES (:t, :e, 'dry_run', :k, :m, :p, :b)
              ON CONFLICT (tenant_id, effect_type, idempotency_key) DO NOTHING"""),
            {
                "t": tenant_id,
                "e": effect,
                "k": key,
                "m": entity,
                "p": self.cipher.encrypt(conn, tenant_id, "dry_run_log.payload", json.dumps(payload)),
                "b": blind_index(self.settings.key("blind_index_key"), email, tenant_id) if email else None,
            },
        )
        write_audit(
            conn,
            action=f"calendar.{effect}.dry_run",
            actor_type="system",
            actor_id="outbound_calendar",
            tenant_id=tenant_id,
            entity_type="meeting",
            entity_id=entity,
        )
