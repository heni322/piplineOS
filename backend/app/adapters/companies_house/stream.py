"""Companies House streaming API: the filing-history stream (free; a "streaming" key from the developer hub).

The stream is one long HTTP response of newline-delimited JSON events (blank lines are heartbeats), every filing
accepted by the register, for every company. We read it in bounded slices (max events / max seconds) from the
last timepoint we saw, and keep only the filings that are buying triggers:
- SH01, "return of allotment of shares": new shares issued, usually a funding round;
- MR01, "registration of a charge": new secured borrowing.

What we take from an event, and nothing more: the company number, the filing type, date and transaction id, and
the capital figure on an SH01. The forms themselves (which can name shareholders) are never downloaded.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from app.adapters.http import check

STREAM_URL = "https://stream.companieshouse.gov.uk"
TRIGGER_TYPES = {"SH01", "MR01"}
_COMPANY = re.compile(r"^/company/([A-Z0-9]{8})/")


@dataclass(frozen=True)
class FilingEvent:
    company_number: str
    filing_type: str
    filed_on: date
    capital: str | None
    transaction_ref: str
    timepoint: int


@dataclass
class StreamSlice:
    events: list[FilingEvent]
    last_timepoint: int | None  # the last timepoint seen, kept or not: where the next slice starts
    read: int = 0
    reset: bool = False  # our timepoint was too old for the stream: restarted from now (alert-worthy gap)


def _capital(data: dict[str, Any]) -> str | None:
    caps = (data.get("description_values") or {}).get("capital") or []
    parts = [
        f"{c.get('currency', '')} {c.get('figure', '')}".strip()
        for c in caps
        if isinstance(c, dict) and c.get("figure")
    ]
    return "; ".join(parts)[:100] or None


def parse_event(line: str) -> tuple[int | None, FilingEvent | None]:
    """(timepoint, the trigger filing or None). Malformed lines and other filing types give (timepoint, None)."""
    try:
        ev = json.loads(line)
    except ValueError:
        return None, None
    if not isinstance(ev, dict):
        return None, None
    tp = (ev.get("event") or {}).get("timepoint")
    timepoint = tp if isinstance(tp, int) else None
    if ev.get("resource_kind") != "filing-history" or (ev.get("event") or {}).get("type") == "deleted":
        return timepoint, None
    data = ev.get("data") or {}
    m = _COMPANY.match(str(ev.get("resource_uri") or ""))  # company_number is not always populated in data
    if m is None or data.get("type") not in TRIGGER_TYPES or timepoint is None:
        return timepoint, None
    try:
        filed = date.fromisoformat(str(data.get("date")))
    except ValueError:
        return timepoint, None
    ref = str(data.get("transaction_id") or ev.get("resource_id") or "")
    if not ref:
        return timepoint, None
    return timepoint, FilingEvent(m.group(1), str(data["type"]), filed, _capital(data), ref[:100], timepoint)


class FilingStream:
    def __init__(
        self, stream_key: str, *, base_url: str = STREAM_URL, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not stream_key:
            raise ValueError("Companies House stream key missing")
        self._http = httpx.Client(
            base_url=base_url,
            transport=transport,
            auth=(stream_key, ""),
            timeout=httpx.Timeout(60.0, connect=10.0),  # heartbeats arrive about every 30 s
            follow_redirects=False,
        )

    def read(self, timepoint: int | None, *, max_events: int = 5000, max_seconds: float = 240.0) -> StreamSlice:
        """Read from `timepoint` (exclusive; None = from now) until max_events or max_seconds, whichever first."""
        out = StreamSlice([], timepoint)
        deadline = time.monotonic() + max_seconds
        params = {"timepoint": timepoint + 1} if timepoint is not None else {}
        with self._http.stream("GET", "/filings", params=params) as resp:
            if resp.status_code == 416:  # timepoint outside the stream's window: restart from now
                resp.read()
                out.reset = True
                return self._restart(out, max_events, deadline)
            if resp.status_code >= 400:
                resp.read()
                check(resp)
            self._consume(resp.iter_lines(), out, max_events, deadline)
        return out

    def _restart(self, out: StreamSlice, max_events: int, deadline: float) -> StreamSlice:
        with self._http.stream("GET", "/filings") as resp:
            if resp.status_code >= 400:
                resp.read()
                check(resp)
            self._consume(resp.iter_lines(), out, max_events, deadline)
        return out

    @staticmethod
    def _consume(lines: Iterator[str], out: StreamSlice, max_events: int, deadline: float) -> None:
        for line in lines:
            if line.strip():
                tp, ev = parse_event(line)
                out.read += 1
                if tp is not None:
                    out.last_timepoint = tp
                if ev is not None:
                    out.events.append(ev)
            if out.read >= max_events or time.monotonic() >= deadline:
                return
