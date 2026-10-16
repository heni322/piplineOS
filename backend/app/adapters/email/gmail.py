"""Live email via the Gmail API, as the sending mailbox (domain-wide delegation), sent as raw MIME so the compliance
headers (List-Unsubscribe, List-Unsubscribe-Post) survive. Gmail may assign its own Message-ID, so after sending we
read back the Message-ID it actually used: replies and bounces are matched on it.

Only the outbound gateway may use this (import contract), and only when all three live keys are on."""

from __future__ import annotations

from app.adapters.email.base import EmailPayload, SendResult, open_before_send
from app.adapters.email.msgraph import build_mime
from app.adapters.google_auth import GMAIL, SCOPE_READ, SCOPE_SEND, GoogleCredentials, b64url
from app.adapters.http import check, check_send
from app.core.logging import get_logger

log = get_logger(__name__)


class GmailSender:
    name = "email.gmail"

    def __init__(self, creds: GoogleCredentials) -> None:
        self.creds = creds

    def send(self, payload: EmailPayload) -> SendResult:
        raw, our_mid = build_mime(payload)
        mailbox = payload.from_address
        with open_before_send(lambda: self.creds.api(GMAIL, mailbox, SCOPE_SEND, SCOPE_READ)) as g:
            resp = check_send(g.post("/users/me/messages/send", json={"raw": b64url(raw)}))
            # From here the email HAS been sent. Nothing below may raise: an error would be read as "retry" and
            # send it twice. If the read-back fails we keep our own Message-ID (replies to it still thread).
            try:
                meta = check(
                    g.get(
                        f"/users/me/messages/{resp.json()['id']}",
                        params={"format": "metadata", "metadataHeaders": "Message-ID"},
                    )
                ).json()
                headers = {h["name"].lower(): h["value"] for h in (meta.get("payload") or {}).get("headers", [])}
            except Exception as exc:
                log.warning("gmail_message_id_readback_failed", error=type(exc).__name__)
                headers = {}
        return SendResult(provider_message_id=headers.get("message-id", our_mid))
