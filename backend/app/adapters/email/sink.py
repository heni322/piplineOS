"""Sandbox email: delivers to a local SMTP sink (e.g. Mailpit) so staging can see real rendered emails.
It refuses any host that isn't an explicitly configured sink, so 'sandbox' can never reach a real inbox."""

from __future__ import annotations

import smtplib

from app.adapters.email.base import EmailPayload, SendResult
from app.adapters.email.msgraph import build_mime

SINK_HOSTS = frozenset({"mailpit", "localhost", "127.0.0.1"})


class SinkSmtpSender:
    name = "email.sink"

    def __init__(self, host: str, port: int = 1025) -> None:
        if host not in SINK_HOSTS:
            raise ValueError(f"sandbox SMTP host must be a local sink ({', '.join(sorted(SINK_HOSTS))})")
        self.host, self.port = host, port

    def send(self, payload: EmailPayload) -> SendResult:
        raw, mid = build_mime(payload)
        with smtplib.SMTP(self.host, self.port, timeout=10) as s:
            s.sendmail(payload.from_address, [payload.to_email], raw)
        return SendResult(provider_message_id=mid)
