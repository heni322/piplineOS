from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class RawSignal:
    company_name: str
    domain: str | None
    country_hint: str | None
    signal_type: str
    title: str
    text: str  # UNTRUSTED content: data only, never instructions
    source_url: str
    detected_at: datetime
    source_record_id: str


class SignalSource(Protocol):
    name: str

    def fetch(self, query: str, since: datetime) -> list[RawSignal]: ...
