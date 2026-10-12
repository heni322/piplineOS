from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.adapters.calendar.base import Slot


class MockCalendar:
    name = "calendar.mock"

    def __init__(self) -> None:
        self.events: dict[str, Slot] = {}

    def free_slots(self, calendar_ref: str, start: datetime, end: datetime) -> list[Slot]:
        slots: list[Slot] = []
        cur = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        while cur + timedelta(minutes=30) <= end and len(slots) < 10:
            s = Slot(cur, cur + timedelta(minutes=30))
            if s not in self.events.values():
                slots.append(s)
            cur += timedelta(hours=2)
        return slots

    def create_event(self, calendar_ref: str, slot: Slot, attendee_email: str, title: str, idempotency_key: str) -> str:
        if idempotency_key in self.events:
            return idempotency_key
        self.events[idempotency_key] = slot
        return f"mockevt-{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)}"
