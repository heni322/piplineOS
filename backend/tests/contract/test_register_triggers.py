"""Contract tests: the Companies House filing stream (parsing, bounded slices, resuming), and the insolvency filter
(Gazette corporate-insolvency notices and register status). In-process httpx.MockTransport only; synthetic data."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from app.adapters.companies_house.stream import FilingStream, parse_event
from app.adapters.insolvency import GazetteNotices, InsolvencyFilter


def event(tp: int, number: str = "01234567", ftype: str = "SH01", **data: Any) -> str:
    return json.dumps(
        {
            "resource_kind": "filing-history",
            "resource_uri": f"/company/{number}/filing-history/TX{tp}",
            "resource_id": f"TX{tp}",
            "data": {
                "category": "capital",
                "date": "2026-10-12",
                "type": ftype,
                "transaction_id": f"TX{tp}",
                "description_values": {"capital": [{"currency": "GBP", "figure": "1,250.00"}]},
                **data,
            },
            "event": {"timepoint": tp, "published_at": "2026-10-12T10:00:00", "type": "changed"},
        }
    )


def test_only_share_allotments_and_charges_are_kept() -> None:
    tp, ev = parse_event(event(10))
    assert tp == 10 and ev is not None
    assert (ev.company_number, ev.filing_type, ev.filed_on, ev.capital) == (
        "01234567",
        "SH01",
        date(2026, 10, 12),
        "GBP 1,250.00",
    )
    assert parse_event(event(11, ftype="MR01"))[1] is not None
    assert parse_event(event(12, ftype="AA")) == (12, None)  # annual accounts: not a trigger
    assert parse_event(event(13, ftype="CS01")) == (13, None)
    deleted = json.loads(event(14))
    deleted["event"]["type"] = "deleted"
    assert parse_event(json.dumps(deleted)) == (14, None)
    assert parse_event("not json") == (None, None)
    assert parse_event(event(15, date="bad")) == (15, None)
    bad_uri = json.loads(event(16))
    bad_uri["resource_uri"] = "/officers/abc"
    assert parse_event(json.dumps(bad_uri)) == (16, None)


def _stream(lines: list[str], seen: list[httpx.Request] | None = None, status: int = 200) -> FilingStream:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        if status == 416 and "timepoint" in req.url.params:
            return httpx.Response(416)
        return httpx.Response(200, content="\n".join(lines).encode())

    return FilingStream("stream-key", transport=httpx.MockTransport(handler))


def test_stream_reads_a_bounded_slice_from_the_cursor() -> None:
    seen: list[httpx.Request] = []
    lines = [event(101), "", event(102, ftype="AA"), event(103, number="SC123456", ftype="MR01"), event(104)]
    sl = _stream(lines, seen).read(100, max_events=3)
    assert seen[0].url.path == "/filings" and seen[0].url.params["timepoint"] == "101"
    assert seen[0].headers["authorization"].startswith("Basic ")
    assert sl.read == 3 and sl.last_timepoint == 103  # the heartbeat is not an event
    assert [(e.company_number, e.filing_type) for e in sl.events] == [("01234567", "SH01"), ("SC123456", "MR01")]


def test_a_too_old_cursor_restarts_from_now_and_says_so() -> None:
    seen: list[httpx.Request] = []
    sl = _stream([event(900)], seen, status=416).read(5)
    assert sl.reset and sl.last_timepoint == 900
    assert "timepoint" not in seen[1].url.params


def test_stream_key_is_required() -> None:
    with pytest.raises(ValueError):
        FilingStream("")


# ---------------------------------------------------------------- insolvency

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def atom(*entries: tuple[str, str, str]) -> bytes:
    body = "".join(
        f"""<entry><id>https://www.thegazette.co.uk/notice/{i}</id><title>{t}</title><updated>{u}</updated>
        <content type="html">{c}</content></entry>"""
        for i, (t, u, c) in enumerate(entries)
    )
    return (
        f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Notices</title>{body}</feed>'.encode()
    )


def _gazette(
    content: bytes, seen: list[httpx.Request] | None = None, status: int = 200, lookback_days: int = 365
) -> GazetteNotices:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        return httpx.Response(status, content=content)

    return GazetteNotices(transport=httpx.MockTransport(handler), lookback_days=lookback_days)


def test_gazette_finds_a_recent_petition_for_this_exact_number() -> None:
    seen: list[httpx.Request] = []
    feed = atom(
        (
            "Petitions to Wind Up (Companies)",
            "2026-09-30T10:00:00Z",
            "LAKESIDE NETWORKS LIMITED, Company Number: 01234567",
        ),
        ("Winding up order", "2026-09-01T10:00:00Z", "OTHER LTD, Company Number: 101234567"),  # another number
        ("Resolutions", "2024-01-01T10:00:00Z", "Company Number: 01234567"),  # too old
    )
    notices = _gazette(feed, seen).recent("01234567", now=NOW)
    assert notices == ["Petitions to Wind Up (Companies)"]
    assert seen[0].url.params["categorycode"] == "G205000000"
    assert seen[0].url.params["text"] == '"01234567"'


def test_gazette_xml_is_parsed_safely() -> None:
    evil = (
        b'<?xml version="1.0"?><!DOCTYPE f [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&x;</title></entry></feed>'
    )
    f = InsolvencyFilter(_gazette(evil), None)
    assert f.is_insolvent("01234567") is None and f.failures == 1  # refused, counted, not expanded


class Register:
    def __init__(self, status: str | None) -> None:
        self.status, self.calls = status, 0

    def profile(self, company_number: str) -> dict[str, Any] | None:
        self.calls += 1
        return None if self.status is None else {"company_status": self.status}


def test_filter_uses_the_gazette_then_the_register_and_caches() -> None:
    petition = atom(("Petitions to Wind Up (Companies)", "2026-10-01T10:00:00Z", "Company Number: 01234567"))
    reg = Register("active")
    f = InsolvencyFilter(_gazette(petition, lookback_days=36500), reg)  # independent of today's date
    assert (f.is_insolvent("1234567") or "").startswith("gazette:")
    assert reg.calls == 0  # the earliest warning is enough

    clean = InsolvencyFilter(_gazette(atom()), Register("liquidation"))
    assert clean.is_insolvent("01234567") == "register_status:liquidation"
    reg2 = Register("active")
    ok = InsolvencyFilter(_gazette(atom()), reg2)
    assert ok.is_insolvent("01234567") is None and ok.is_insolvent("01234567") is None
    assert reg2.calls == 1  # cached for the run
    assert ok.is_insolvent("not a number") is None


def test_a_failing_gazette_falls_back_to_the_register() -> None:
    f = InsolvencyFilter(_gazette(b"", status=503), Register("administration"))
    assert f.is_insolvent("01234567") == "register_status:administration" and f.failures == 1
