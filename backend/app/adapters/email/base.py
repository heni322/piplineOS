from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.adapters.base import RetryableProviderError


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


def open_before_send[T](open_session: Callable[[], T]) -> T:
    """Opens the provider session (access token, client) BEFORE anything is handed to the provider. Nothing can have
    been sent yet, so any failure here is retryable (bounded by the gateway's attempt limit)."""
    try:
        return open_session()
    except RetryableProviderError:
        raise
    except Exception as exc:
        raise RetryableProviderError(f"provider session unavailable: {type(exc).__name__}") from exc
