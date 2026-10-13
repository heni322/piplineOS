"""Live calendar via Microsoft Graph: free/busy, create (idempotent via transactionId), cancel."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.adapters.calendar.base import Slot
from app.adapters.http import ProviderError, check
from app.adapters.msgraph_auth import GraphCredentials


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def transaction_id(idempotency_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pipelineos:{idempotency_key}"))


class GraphCalendar:
    name = "calendar.msgraph"

    def __init__(self, creds: GraphCredentials, mailbox: str) -> None:
        self.creds, self.mailbox = creds, mailbox

    def busy(self, calendar_ref: str, start: datetime, end: datetime) -> list[Slot]:
        with self.creds.graph() as g:
            data = check(
                g.post(
                    f"/users/{self.mailbox}/calendar/getSchedule",
                    json={
                        "schedules": [self.mailbox],
                        "startTime": {"dateTime": _iso(start), "timeZone": "UTC"},
                        "endTime": {"dateTime": _iso(end), "timeZone": "UTC"},
                        "availabilityViewInterval": 15,
                    },
                )
            ).json()
        out = []
        for sched in data.get("value", []):
            for item in sched.get("scheduleItems", []):
                if item.get("status") in ("busy", "tentative", "oof", "workingElsewhere"):
                    out.append(
                        Slot(
                            datetime.fromisoformat(item["start"]["dateTime"][:19]).replace(tzinfo=UTC),
                            datetime.fromisoformat(item["end"]["dateTime"][:19]).replace(tzinfo=UTC),
                        )
                    )
        return out

    def create_event(self, calendar_ref: str, slot: Slot, attendee_email: str, title: str, idempotency_key: str) -> str:
        with self.creds.graph() as g:
            ev = check(
                g.post(
                    f"/users/{self.mailbox}/events",
                    json={
                        "subject": title,
                        "transactionId": transaction_id(idempotency_key),
                        "start": {"dateTime": _iso(slot.starts_at), "timeZone": "UTC"},
                        "end": {"dateTime": _iso(slot.ends_at), "timeZone": "UTC"},
                        "attendees": [{"emailAddress": {"address": attendee_email}, "type": "required"}],
                        "isOnlineMeeting": True,
                        "onlineMeetingProvider": "teamsForBusiness",
                        "allowNewTimeProposals": True,
                    },
                )
            ).json()
        return str(ev["id"])

    def cancel_event(self, calendar_ref: str, provider_event_id: str, idempotency_key: str) -> None:
        with self.creds.graph() as g:
            try:
                check(
                    g.post(
                        f"/users/{self.mailbox}/events/{provider_event_id}/cancel",
                        json={"comment": "This meeting has been moved or cancelled."},
                    )
                )
            except ProviderError as exc:
                if exc.status != 404:  # already gone: cancelling is idempotent
                    raise
