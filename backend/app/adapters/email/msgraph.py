"""Live email via Microsoft Graph, sent as MIME so the compliance headers (List-Unsubscribe, List-Unsubscribe-Post,
our own Message-ID) survive: Graph's JSON sendMail only allows custom X- headers.

Only the outbound gateway may use this (import contract), and only when all three live keys are on."""

from __future__ import annotations

import base64
import hashlib
from email.message import EmailMessage
from email.utils import formataddr, formatdate

from app.adapters.email.base import EmailPayload, SendResult, open_before_send
from app.adapters.http import check_send
from app.adapters.msgraph_auth import GraphCredentials


def message_id(idempotency_key: str, domain: str) -> str:
    """Deterministic Message-ID: a retry with the same key produces the same id (threading + provider dedupe)."""
    return f"<{hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]}@{domain}>"


def build_mime(p: EmailPayload) -> tuple[bytes, str]:
    domain = p.from_address.split("@", 1)[1]
    msg = EmailMessage()
    mid = message_id(p.idempotency_key, domain)
    msg["Message-ID"] = mid
    msg["Date"] = formatdate(localtime=False)
    msg["From"] = formataddr((p.from_name, p.from_address))
    msg["To"] = p.to_email
    msg["Subject"] = p.subject
    for k, v in p.headers.items():
        if k.lower() in {"message-id", "from", "to", "subject", "date", "bcc", "cc"}:
            continue  # never let caller headers override addressing
        msg[k] = v
    msg.set_content(p.body_text, charset="utf-8")
    return msg.as_bytes(), mid


class GraphEmailSender:
    name = "email.msgraph"

    def __init__(self, creds: GraphCredentials) -> None:
        self.creds = creds

    def send(self, payload: EmailPayload) -> SendResult:
        raw, mid = build_mime(payload)
        with open_before_send(self.creds.graph) as g:
            check_send(
                g.post(
                    f"/users/{payload.from_address}/sendMail",
                    content=base64.b64encode(raw),
                    headers={"Content-Type": "text/plain"},
                )
            )
        return SendResult(provider_message_id=mid)
