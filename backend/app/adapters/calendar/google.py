"""Live calendar via Google Calendar, as the inbox's mailbox: free/busy, create (idempotent: the event id is derived
from our idempotency key, so a retry gets 409 instead of a second meeting), cancel (gone already = fine)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.adapters.calendar.base import Slot
from app.adapters.google_auth import CALENDAR, SCOPE_EVENTS, SCOPE_FREEBUSY, GoogleCredentials
from app.adapters.http import ProviderError, check

_BASE32HEX = "0123456789abcdefghijklmnopqrstuv"


def event_id(idempotency_key: str) -> str:
    """Google event ids use base32hex characters (0-9, a-v), 5 to 1024 long: 52 chars from a SHA-256."""
    digest = int.from_bytes(hashlib.sha256(f"pipelineos:{idempotency_key}".encode()).digest(), "big")
    out = []
    for _ in range(52):
        digest, r = divmod(digest, 32)
        out.append(_BASE32HEX[r])
    return "pos" + "".join(out)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


class GoogleCalendar:
    name = "calendar.google"

    def __init__(self, creds: GoogleCredentials, mailbox: str) -> None:
        self.creds, self.mailbox = creds, mailbox

    def busy(self, calendar_ref: str, start: datetime, end: datetime) -> list[Slot]:
        with self.creds.api(CALENDAR, self.mailbox, SCOPE_FREEBUSY) as g:
            data = check(
                g.post(
                    "/freeBusy", json={"timeMin": _iso(start), "timeMax": _iso(end), "items": [{"id": self.mailbox}]}
                )
            ).json()
        cal = (data.get("calendars") or {}).get(self.mailbox) or {}
        if cal.get("errors"):
            raise ProviderError(502, f"free/busy unavailable: {cal['errors'][0].get('reason', 'unknown')}")
        return [Slot(_parse(b["start"]), _parse(b["end"])) for b in cal.get("busy", [])]

    def create_event(self, calendar_ref: str, slot: Slot, attendee_email: str, title: str, idempotency_key: str) -> str:
        eid = event_id(idempotency_key)
        body = {
            "id": eid,
            "summary": title,
            "start": {"dateTime": _iso(slot.starts_at), "timeZone": "UTC"},
            "end": {"dateTime": _iso(slot.ends_at), "timeZone": "UTC"},
            "attendees": [{"email": attendee_email}],
            "guestsCanModify": False,
            "conferenceData": {"createRequest": {"requestId": eid, "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
        }
        with self.creds.api(CALENDAR, self.mailbox, SCOPE_EVENTS) as g:
            try:
                check(
                    g.post(
                        "/calendars/primary/events",
                        params={"sendUpdates": "all", "conferenceDataVersion": 1},
                        json=body,
                    )
                )
            except ProviderError as exc:
                if exc.status != 409:  # 409 = this exact event already exists (an earlier attempt succeeded)
                    raise
        return eid

    def cancel_event(self, calendar_ref: str, provider_event_id: str, idempotency_key: str) -> None:
        with self.creds.api(CALENDAR, self.mailbox, SCOPE_EVENTS) as g:
            try:
                check(g.delete(f"/calendars/primary/events/{provider_event_id}", params={"sendUpdates": "all"}))
            except ProviderError as exc:
                if exc.status not in (404, 410):  # already gone: cancelling is idempotent
                    raise
