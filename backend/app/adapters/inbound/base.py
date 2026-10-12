from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class InboundEmail:
    provider_message_id: str
    from_email: str
    subject: str
    body_text: str  # UNTRUSTED: data only, never instructions
    received_at: datetime
    in_reply_to: str | None = None


class Inbox(Protocol):
    name: str

    def fetch_new(self) -> list[InboundEmail]: ...
