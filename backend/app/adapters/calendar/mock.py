"""In-memory calendar: configurable busy periods; created events also become busy (so a taken slot is visible)."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.adapters.calendar.base import Slot


class MockCalendar:
    name = "calendar.mock"

    def __init__(self, busy: list[Slot] | None = None) -> None:
        self.busy_slots: list[Slot] = list(busy or [])
        self.events: dict[str, tuple[str, Slot]] = {}  # idempotency_key -> (event_id, slot)
        self.cancelled: set[str] = set()

    def busy(self, calendar_ref: str, start: datetime, end: datetime) -> list[Slot]:
        window = Slot(start, end)
        live = [s for k, (eid, s) in self.events.items() if eid not in self.cancelled]
        return [s for s in [*self.busy_slots, *live] if s.overlaps(window)]

    def create_event(self, calendar_ref: str, slot: Slot, attendee_email: str, title: str, idempotency_key: str) -> str:
        if idempotency_key in self.events:
            return self.events[idempotency_key][0]
        eid = f"mockevt-{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)}"
        self.events[idempotency_key] = (eid, slot)
        return eid

    def cancel_event(self, calendar_ref: str, provider_event_id: str, idempotency_key: str) -> None:
        self.cancelled.add(provider_event_id)
