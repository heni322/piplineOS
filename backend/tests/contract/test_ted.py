"""Contract tests for TED EU award notices (search API v3): request shape, multilingual fields, keyword filtering,
paging, countries. In-process httpx.MockTransport; synthetic notices."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.adapters.registry import live_sources
from app.adapters.signals.ted import TedAwardSource
from app.demo.dataset import DEMO_CONFIG
from app.domain.tenants.config import TenantConfig

SINCE = datetime(2026, 10, 1, tzinfo=UTC)


def notice(no: str, title: Any, winners: Any, countries: Any = None) -> dict[str, Any]:
    return {
        "publication-number": no,
        "publication-date": "2026-10-09+02:00",
        "notice-title": title,
        "buyer-name": {"fra": ["Ville de Lyon"]},
        "winner-name": winners,
        "organisation-country-tenderer": countries or ["FRA"],
    }


PAGE = {
    "notices": [
        notice(
            "612345-2026",
            {"eng": "IT support services", "fra": "Services de support informatique"},
            {"fra": ["Contoso Informatique SAS", "Fabrikam SARL"]},
            ["FRA", "BEL"],
        ),
        notice("612346-2026", {"eng": "School catering"}, {"fra": ["Cantine SA"]}),
        notice("612347-2026", "Managed service for the IT service desk of a school", ["Northwind SA"]),
    ],
    "totalNoticeCount": 3,
}


def test_award_notices_become_tender_signals() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=PAGE)

    sigs = TedAwardSource(["GB", "FR", "BE"], transport=httpx.MockTransport(handler)).fetch(
        "IT support; managed service; -school", SINCE
    )
    body = json.loads(seen[0].content)
    assert seen[0].method == "POST" and seen[0].url.path == "/v3/notices/search"
    assert "publication-date>=20261001" in body["query"] and "(FRA BEL)" in body["query"]  # GB is not on TED
    assert "winner-name" in body["fields"] and not any("contact" in f for f in body["fields"])
    assert len(seen) == 1  # a short page ends paging
    assert [(s.company_name, s.country_hint) for s in sigs] == [
        ("Contoso Informatique SAS", "France"),
        ("Fabrikam SARL", "Belgium"),
    ]
    s = sigs[0]
    assert s.signal_type == "tender" and s.title == "Awarded: IT support services"
    assert s.source_url == "https://ted.europa.eu/en/notice/-/detail/612345-2026"
    assert s.detected_at.date().isoformat() == "2026-10-09" and s.source_record_id == "ted:612345-2026:0"
    assert "Ville de Lyon" in s.text


def test_paging_and_nothing_without_eu_countries_or_keywords() -> None:
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(json.loads(req.content)["page"])
        full = {"notices": [notice(f"{i}-2026", "IT support", ["X"]) for i in range(2)]}
        return httpx.Response(200, json=full)

    src = TedAwardSource(["FR"], transport=httpx.MockTransport(handler), page_size=2, max_pages=3)
    assert len(src.fetch("IT support", SINCE)) == 6 and calls == [1, 2, 3]
    assert TedAwardSource(["GB"]).fetch("IT support", SINCE) == []
    assert TedAwardSource(["FR"]).fetch("", SINCE) == []


def test_ted_is_added_for_clients_targeting_eu_countries() -> None:
    data = DEMO_CONFIG.model_dump()
    data["icp"]["countries"] = ["GB", "FR"]
    names = [b.source.name for b in live_sources(TenantConfig.model_validate(data))]
    assert names == ["contracts_finder.live", "find_a_tender.live", "ted.live"]
