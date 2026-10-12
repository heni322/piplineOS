"""Billing eligibility (attended -> billed). Pure: no database, no clock. Every rule returns a reason code, so an
excluded meeting always says why (docs/04-state-machine.md §3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class MeetingFacts:
    booked_by_system: bool
    meeting_status: str
    attendance_status: str | None  # latest attendance event
    attendance_source: str | None  # calendar | crm | tenant_confirmation | operator
    qualification_met: bool | None
    criteria_hash: str | None  # hash of the qualification criteria the lead was qualified against
    booked_at: datetime
    signed_criteria: list[tuple[str, datetime]] = field(default_factory=list)  # (hash, signed_at)
    already_billed: bool = False
    per_meeting_minor: int = 0
    pricing_signed: bool = False
    dispute_status: str | None = None  # open | upheld | rejected (latest)


CONFIRMED_SOURCES = frozenset({"calendar", "crm", "tenant_confirmation"})


def eligibility(f: MeetingFacts) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not f.booked_by_system:
        reasons.append("NOT_BOOKED_BY_SYSTEM")
    if f.meeting_status != "attended" or f.attendance_status != "attended":
        reasons.append("NOT_ATTENDED")
    elif f.attendance_source not in CONFIRMED_SOURCES:
        # Our own operators can't make a meeting billable: attendance must come from the calendar/CRM or the client.
        reasons.append("ATTENDANCE_NOT_CONFIRMED_BY_CLIENT_OR_CALENDAR")
    if f.qualification_met is not True:
        reasons.append("NOT_QUALIFIED")
    elif f.criteria_hash is None or not any(h == f.criteria_hash and at <= f.booked_at for h, at in f.signed_criteria):
        reasons.append("CRITERIA_NOT_SIGNED_AT_BOOKING")
    if f.already_billed:
        reasons.append("ALREADY_BILLED")
    if f.per_meeting_minor <= 0:
        reasons.append("NO_PER_MEETING_FEE")
    elif not f.pricing_signed:
        reasons.append("PRICING_NOT_SIGNED")
    if f.dispute_status == "open":
        reasons.append("DISPUTE_OPEN")
    elif f.dispute_status == "upheld":
        reasons.append("DISPUTE_UPHELD")
    return (not reasons), reasons
