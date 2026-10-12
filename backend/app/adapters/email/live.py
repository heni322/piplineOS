from __future__ import annotations

from app.adapters.base import AdapterNotConfigured
from app.adapters.email.base import EmailPayload, SendResult


class LiveEmailSender:
    """Real provider integration — chosen and implemented in Phase 6 (provider decision pending)."""

    name = "email.live"

    def send(self, payload: EmailPayload) -> SendResult:
        raise AdapterNotConfigured("live email sending is not implemented until Phase 6")
