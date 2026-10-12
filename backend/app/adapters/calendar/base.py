from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Slot:
    starts_at: datetime
    ends_at: datetime

    def overlaps(self, other: Slot) -> bool:
        return self.starts_at < other.ends_at and other.starts_at < self.ends_at


class CalendarAdapter(Protocol):
    name: str

    def busy(self, calendar_ref: str, start: datetime, end: datetime) -> list[Slot]: ...
    def create_event(
        self, calendar_ref: str, slot: Slot, attendee_email: str, title: str, idempotency_key: str
    ) -> str: ...
    def cancel_event(self, calendar_ref: str, provider_event_id: str, idempotency_key: str) -> None: ...
