from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class EmailPayload:
    to_email: str
    from_address: str
    from_name: str
    subject: str
    body_text: str
    idempotency_key: str
    headers: dict[str, str] = field(default_factory=dict)
    provider: str = "mock"  # the sending inbox's provider (msgraph / google): picks the live sender


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str
    accepted: bool = True


class EmailSender(Protocol):
    name: str

    def send(self, payload: EmailPayload) -> SendResult: ...
