"""One mapping from the emergency-stop and pilot-limit domain errors to HTTP answers, for every router that uses
app.domain.tenants.controls (clients and campaigns)."""

from __future__ import annotations

from app.core.errors import ProblemError
from app.domain.tenants.controls import ControlError

_ANSWERS = {
    "NOT_ACTIVE": (409, "Only an active client or campaign can be paused"),
    "NOT_PAUSED": (409, "It is not paused"),
    "NOT_FOUND": (404, "Not found"),
    "REASON_REQUIRED": (422, "Give a reason"),
    "OUT_OF_RANGE": (422, "Choose between 1 and 100000, or no limit"),
}


def problem_for(exc: ControlError) -> ProblemError:
    status, title = _ANSWERS.get(exc.code, (409, exc.code))
    return ProblemError(status, title)
