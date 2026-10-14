"""Inbound replies from Gmail: a Pub/Sub push says "this mailbox changed, up to historyId N"; we list the messages
added to the inbox since the last historyId we processed and fetch each one as untrusted data.
The watch (users.watch) lasts 7 days and is renewed daily."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from app.adapters.google_auth import GMAIL, SCOPE_READ, GoogleCredentials, b64url_decode
from app.adapters.http import ProviderError, check
from app.adapters.inbound.base import InboundEmail

MAX_BODY = 200_000


def parse_push(body: dict[str, object]) -> tuple[str, int] | None:
    """Pub/Sub push body -> (mailbox, historyId), or None if it isn't a Gmail notification."""
    msg = body.get("message")
    if not isinstance(msg, dict) or not isinstance(msg.get("data"), str):
        return None
    try:
        data = json.loads(base64.b64decode(msg["data"]))
        return str(data["emailAddress"]), int(data["historyId"])
    except (ValueError, KeyError, TypeError):
        return None


def parse_raw(raw: bytes) -> InboundEmail:
    m = BytesParser(policy=policy.default).parsebytes(raw)
    addrs = getaddresses([str(m.get("From", ""))])
    part = m.get_body(preferencelist=("plain",))
    text = part.get_content() if part is not None else ""
    try:
        received = parsedate_to_datetime(str(m["Date"])).astimezone(UTC)
    except (TypeError, ValueError):
        received = datetime.now(UTC)
    return InboundEmail(
        provider_message_id=str(m.get("Message-ID", "")).strip(),
        from_email=addrs[0][1] if addrs else "",
        subject=str(m.get("Subject", "")),
        body_text=str(text)[:MAX_BODY],
        received_at=received,
        in_reply_to=str(m["In-Reply-To"]).strip() if m.get("In-Reply-To") else None,
    )


def watch(creds: GoogleCredentials, mailbox: str, topic: str) -> int:
    """Starts (or renews) push notifications for the inbox; returns the current historyId."""
    with creds.api(GMAIL, mailbox, SCOPE_READ) as g:
        r = check(
            g.post(
                "/users/me/watch", json={"topicName": topic, "labelIds": ["INBOX"], "labelFilterBehavior": "include"}
            )
        )
    return int(r.json()["historyId"])


class GmailInbox:
    name = "inbox.gmail"

    def __init__(self, creds: GoogleCredentials, mailbox: str) -> None:
        self.creds, self.mailbox = creds, mailbox

    def fetch_new(self) -> list[InboundEmail]:  # push-based: see fetch_since
        return []

    def recent_ids(self, since: datetime, limit: int = 200) -> list[tuple[str, str]]:
        """Safety-net sweep: (Gmail id, Message-ID) of INBOX messages received since `since`. Headers only; a message
        is fetched in full only if it was never processed. Also covers a history cursor that became too old."""
        out: list[tuple[str, str]] = []
        page: str | None = None
        with self.creds.api(GMAIL, self.mailbox, SCOPE_READ) as g:
            ids: list[str] = []
            while len(ids) < limit:
                params: dict[str, str | int] = {
                    "q": f"after:{int(since.timestamp())}",
                    "labelIds": "INBOX",
                    "maxResults": min(100, limit),
                }
                if page:
                    params["pageToken"] = page
                data = check(g.get("/users/me/messages", params=params)).json()
                ids += [str(m["id"]) for m in data.get("messages", [])]
                page = data.get("nextPageToken")
                if not page:
                    break
            for mid in ids[:limit]:
                meta = check(
                    g.get(f"/users/me/messages/{mid}", params={"format": "metadata", "metadataHeaders": "Message-ID"})
                ).json()
                headers = {h["name"].lower(): h["value"] for h in (meta.get("payload") or {}).get("headers", [])}
                if "SENT" not in meta.get("labelIds", []):
                    out.append((mid, str(headers.get("message-id", "")).strip()))
        return out

    def fetch_message(self, message_id: str) -> InboundEmail:
        with self.creds.api(GMAIL, self.mailbox, SCOPE_READ) as g:
            raw = check(g.get(f"/users/me/messages/{message_id}", params={"format": "raw"})).json()["raw"]
        return parse_raw(b64url_decode(raw))

    def fetch_since(self, start_history_id: int, limit: int = 100) -> tuple[list[InboundEmail], int]:
        """Messages added to INBOX after start_history_id, and the newest historyId seen.
        If the start point is too old (404), returns nothing and the caller restarts from the current historyId."""
        ids: list[str] = []
        newest = start_history_id
        page: str | None = None
        with self.creds.api(GMAIL, self.mailbox, SCOPE_READ) as g:
            try:
                while len(ids) < limit:
                    params: dict[str, str | int] = {
                        "startHistoryId": start_history_id,
                        "historyTypes": "messageAdded",
                        "labelId": "INBOX",
                    }
                    if page:
                        params["pageToken"] = page
                    data = check(g.get("/users/me/history", params=params)).json()
                    newest = max(newest, int(data.get("historyId", newest)))
                    for h in data.get("history", []):
                        for added in h.get("messagesAdded", []):
                            mid = added["message"]["id"]
                            if mid not in ids and "SENT" not in added["message"].get("labelIds", []):
                                ids.append(mid)
                    page = data.get("nextPageToken")
                    if not page:
                        break
            except ProviderError as exc:
                if exc.status == 404:
                    return [], newest
                raise
            out = []
            for mid in ids[:limit]:
                raw = check(g.get(f"/users/me/messages/{mid}", params={"format": "raw"})).json()["raw"]
                out.append(parse_raw(b64url_decode(raw)))
        return out, newest
