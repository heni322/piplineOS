from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Slot:
    starts_at: datetime
    ends_at: datetime


class CalendarAdapter(Protocol):
    name: str

    def free_slots(self, calendar_ref: str, start: datetime, end: datetime) -> list[Slot]: ...
    def create_event(
        self, calendar_ref: str, slot: Slot, attendee_email: str, title: str, idempotency_key: str
    ) -> str: ...
