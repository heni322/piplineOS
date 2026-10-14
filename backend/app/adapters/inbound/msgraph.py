"""Inbound replies from Microsoft Graph: fetch one message (from a change notification) as untrusted data, and
validate change notifications (clientState, constant-time)."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from app.adapters.http import check
from app.adapters.inbound.base import InboundEmail
from app.adapters.msgraph_auth import GraphCredentials

FIELDS = "internetMessageId,from,subject,body,receivedDateTime,internetMessageHeaders"


MAX_SUBSCRIPTION_MINUTES = 4200  # Graph allows just under 3 days for message subscriptions


def client_state(secret: bytes, mailbox: str) -> str:
    """Per-mailbox secret echoed back by Graph: authenticates a notification AND says which mailbox it is for."""
    return hmac.new(secret, f"graph-notify:{mailbox.lower()}".encode(), hashlib.sha256).hexdigest()


def mailbox_for(received_state: str | None, secret: bytes, mailboxes: list[str]) -> str | None:
    if not received_state:
        return None
    for mb in mailboxes:  # constant-time per candidate
        if hmac.compare_digest(received_state.encode(), client_state(secret, mb).encode()):
            return mb
    return None


def subscribe(
    creds: GraphCredentials, mailbox: str, notification_url: str, secret: bytes, now: datetime | None = None
) -> str:
    expires = (now or datetime.now(UTC)) + timedelta(minutes=MAX_SUBSCRIPTION_MINUTES)
    with creds.graph() as g:
        sub = check(
            g.post(
                "/subscriptions",
                json={
                    "changeType": "created",
                    "notificationUrl": notification_url,
                    "resource": f"/users/{mailbox}/mailFolders('Inbox')/messages",
                    "expirationDateTime": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "clientState": client_state(secret, mailbox),
                },
            )
        ).json()
    return str(sub["id"])


def renew_all(creds: GraphCredentials, now: datetime | None = None, within_hours: int = 24) -> int:
    """Extends every subscription of this app that expires within `within_hours`."""
    now = now or datetime.now(UTC)
    renewed = 0
    with creds.graph() as g:
        for sub in check(g.get("/subscriptions")).json().get("value", []):
            exp = datetime.fromisoformat(sub["expirationDateTime"].replace("Z", "+00:00"))
            if exp - now < timedelta(hours=within_hours):
                new = now + timedelta(minutes=MAX_SUBSCRIPTION_MINUTES)
                check(
                    g.patch(
                        f"/subscriptions/{sub['id']}", json={"expirationDateTime": new.strftime("%Y-%m-%dT%H:%M:%SZ")}
                    )
                )
                renewed += 1
    return renewed


class GraphInbox:
    name = "inbox.msgraph"

    def __init__(self, creds: GraphCredentials, mailbox: str) -> None:
        self.creds, self.mailbox = creds, mailbox

    def fetch_new(self) -> list[InboundEmail]:  # push-based: messages arrive through notifications
        return []

    def recent_ids(self, since: datetime, limit: int = 200) -> list[tuple[str, str]]:
        """Safety-net sweep: (Graph id, Message-ID) of Inbox messages received since `since`, newest first. Only ids
        are read here; a message is fetched in full only if it was never processed (missed notification)."""
        out: list[tuple[str, str]] = []
        params: dict[str, str | int] | None = {
            "$filter": f"receivedDateTime ge {since.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "$select": "id,internetMessageId",
            "$orderby": "receivedDateTime desc",
            "$top": 50,
        }
        url = f"/users/{self.mailbox}/mailFolders('Inbox')/messages"
        with self.creds.graph() as g:
            while url and len(out) < limit:
                data = check(g.get(url, params=params)).json()
                out += [(str(m["id"]), str(m.get("internetMessageId") or "")) for m in data.get("value", [])]
                url, params = data.get("@odata.nextLink"), None  # the next link carries its own query
        return out[:limit]

    def fetch_message(self, message_id: str) -> InboundEmail:
        with self.creds.graph() as g:
            m = check(
                g.get(
                    f"/users/{self.mailbox}/messages/{message_id}",
                    params={"$select": FIELDS},
                    headers={"Prefer": 'outlook.body-content-type="text"'},
                )
            ).json()
        headers = {h["name"].lower(): h["value"] for h in m.get("internetMessageHeaders") or []}
        return InboundEmail(
            provider_message_id=m["internetMessageId"],
            from_email=m["from"]["emailAddress"]["address"],
            subject=m.get("subject") or "",
            body_text=(m.get("body") or {}).get("content", ""),
            received_at=datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")),
            in_reply_to=headers.get("in-reply-to"),
        )
