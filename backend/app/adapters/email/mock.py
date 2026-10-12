"""In-memory mock sender used by tests to exercise the 'live' code path without any network."""

from __future__ import annotations

import uuid

from app.adapters.email.base import EmailPayload, SendResult


class MockEmailSender:
    name = "email.mock"

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.sent: list[EmailPayload] = []
        self._by_key: dict[str, SendResult] = {}
        self.fail_with = fail_with

    def send(self, payload: EmailPayload) -> SendResult:
        if self.fail_with:
            raise self.fail_with
        if payload.idempotency_key in self._by_key:  # provider-side idempotency
            return self._by_key[payload.idempotency_key]
        self.sent.append(payload)
        res = SendResult(provider_message_id=f"mock-{uuid.uuid4()}")
        self._by_key[payload.idempotency_key] = res
        return res
