"""Contract tests for the free sources: public job boards (Greenhouse, Lever, Ashby) and Companies House officers
(leadership-change signals and decision-makers), plus FREE_ONLY wiring. Every request goes to an in-process
httpx.MockTransport; fixtures are synthetic (fictional companies and people, .example domains) in the providers'
real shape."""

from __future__ import annotations

import base64
import secrets
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.adapters.base import RateLimited
from app.adapters.companies_house.officers import (
    CompaniesHouseOfficers,
    OfficerAppointmentSource,
    WatchedCompany,
    display_name,
)
from app.adapters.registry import live_sources
from app.adapters.signals.job_boards import JobBoardSource, WatchedBoard, parse_board
from app.demo.dataset import DEMO_CONFIG
from app.domain.tenants.config import TenantConfig
from app.llm.backends import AnthropicBackend, OllamaBackend
from app.llm.factory import select_backend
from app.settings import Settings

SINCE = datetime(2026, 10, 1, tzinfo=UTC)


def _json(payload: Any, status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda req: httpx.Response(status, json=payload))


# ---------------------------------------------------------------- job boards

GREENHOUSE = {
    "jobs": [
        {
            "id": 101,
            "title": "Service Desk Analyst",
            "updated_at": "2026-10-10T09:00:00-04:00",
            "first_published": "2026-10-09T09:00:00-04:00",
            "location": {"name": "Leeds, UK"},
            "absolute_url": "https://job-boards.greenhouse.io/contoso/jobs/101",
            "departments": [{"name": "IT Operations"}],
        },
        {  # too old
            "id": 102,
            "title": "Service Desk Lead",
            "first_published": "2026-08-01T09:00:00Z",
            "location": {"name": "Leeds"},
            "absolute_url": "https://job-boards.greenhouse.io/contoso/jobs/102",
        },
        {  # excluded keyword
            "id": 103,
            "title": "Service Desk Intern",
            "first_published": "2026-10-11T09:00:00Z",
            "location": {"name": "Leeds"},
        },
        {  # not a hiring keyword
            "id": 104,
            "title": "Office Manager",
            "first_published": "2026-10-11T09:00:00Z",
            "location": {"name": "Leeds"},
        },
    ],
    "meta": {"total": 4},
}
LEVER = [
    {
        "id": "a1b2",
        "text": "IT Support Engineer",
        "categories": {"team": "Infrastructure", "location": "London", "commitment": "Full-time"},
        "createdAt": int(datetime(2026, 10, 12, 8, tzinfo=UTC).timestamp() * 1000),
        "hostedUrl": "https://jobs.lever.co/fabrikam/a1b2",
        "lists": [{"text": "Hiring manager", "content": "Jo Private"}],
    }
]
ASHBY = {
    "jobs": [
        {
            "id": "z9",
            "title": "Head of IT Support",
            "department": "Operations",
            "location": "Manchester",
            "publishedAt": "2026-10-13T10:00:00.000+00:00",
            "jobUrl": "https://jobs.ashbyhq.com/northwind/z9",
            "isListed": True,
        },
        {
            "id": "z8",
            "title": "IT Support (unlisted)",
            "publishedAt": "2026-10-13T10:00:00Z",
            "isListed": False,
        },
    ]
}


def test_board_names_are_validated() -> None:
    assert parse_board("greenhouse:contoso") == ("greenhouse", "contoso")
    assert parse_board("Lever-EU: fabrikam") == ("lever-eu", "fabrikam")
    for bad in ("workday:x", "greenhouse:", "greenhouse:../admin", "ashby:a/b", "lever:.hidden", "contoso"):
        assert parse_board(bad) is None


def test_job_boards_give_one_hiring_signal_per_company() -> None:
    seen: list[str] = []

    def gh(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, json=GREENHOUSE)

    src = JobBoardSource(
        [
            WatchedBoard("Contoso Managed IT Ltd", "greenhouse:contoso", "contoso-it.example", "01234567", "GB"),
            WatchedBoard("Fabrikam Ltd", "lever:fabrikam"),
            WatchedBoard("Northwind Ltd", "ashby:northwind"),
        ],
        transports={"greenhouse": httpx.MockTransport(gh), "lever": _json(LEVER), "ashby": _json(ASHBY)},
    )
    out = {s.company_name: s for s in src.fetch("service desk; IT support; -intern", SINCE)}
    assert seen == ["https://boards-api.greenhouse.io/v1/boards/contoso/jobs"]
    assert set(out) == {"Contoso Managed IT Ltd", "Fabrikam Ltd", "Northwind Ltd"}

    c = out["Contoso Managed IT Ltd"]
    assert c.signal_type == "job_posting" and c.domain == "contoso-it.example" and c.company_number == "01234567"
    assert "Service Desk Analyst (IT Operations), Leeds, UK" in c.text
    assert "Lead" not in c.text and "Intern" not in c.text and "Office Manager" not in c.text
    assert c.source_url == "https://job-boards.greenhouse.io/contoso/jobs/101"  # one role: its own page
    assert c.detected_at == datetime(2026, 10, 9, 13, tzinfo=UTC)

    f = out["Fabrikam Ltd"]
    assert "IT Support Engineer (Infrastructure), London" in f.text
    assert "Jo Private" not in f.text  # nothing about individuals is read
    assert f.country_hint == "London"

    n = out["Northwind Ltd"]
    assert "Head of IT Support" in n.text and "unlisted" not in n.text


def test_empty_query_counts_every_new_role_and_ids_are_stable() -> None:
    src = JobBoardSource([WatchedBoard("Contoso", "greenhouse:contoso")], transports={"greenhouse": _json(GREENHOUSE)})
    [sig] = src.fetch("", SINCE)
    assert "3 new role(s)" in sig.text
    assert sig.source_url == "https://job-boards.greenhouse.io/contoso"  # several roles: the board page
    assert src.fetch("", SINCE)[0].source_record_id == sig.source_record_id  # same roles -> same record (dedupe)


def test_a_failing_board_does_not_stop_the_others() -> None:
    src = JobBoardSource(
        [
            WatchedBoard("Gone Ltd", "greenhouse:gone"),
            WatchedBoard("Bad", "workday:bad"),
            WatchedBoard("Fabrikam Ltd", "lever:fabrikam"),
        ],
        transports={"greenhouse": _json({"error": "not found"}, 404), "lever": _json(LEVER)},
    )
    assert [s.company_name for s in src.fetch("", SINCE)] == ["Fabrikam Ltd"]
    assert src.failed == ["greenhouse:gone", "workday:bad"]


def test_a_rate_limited_board_is_reported() -> None:
    src = JobBoardSource([WatchedBoard("C", "ashby:c")], transports={"ashby": _json({}, 429)})
    with pytest.raises(RateLimited):  # the hunt counts it and retries on the next run
        src.fetch("", SINCE)


# ---------------------------------------------------------------- Companies House officers


def _officer(name: str, appointed: str, role: str = "director", occupation: str = "", **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "officer_role": role,
        "appointed_on": appointed,
        "occupation": occupation,
        "date_of_birth": {"month": 4, "year": 1980},
        "nationality": "British",
        "country_of_residence": "England",
        "address": {"premises": "1", "address_line_1": "Private Road", "locality": "Leeds"},
        "links": {"self": f"/company/01234567/appointments/{name[:4]}", "officer": {"appointments": "/officers/x"}},
        **extra,
    }


OFFICERS = {
    "active_count": 3,
    "items": [
        _officer("PATEL, Asha", "2026-10-06", occupation="Chief Technology Officer"),
        _officer("JONES, Sam", "2019-05-01", occupation="Managing Director"),
        _officer("EVANS, Old", "2015-01-01", resigned_on="2026-10-07"),
        _officer("SECRETARIAL SERVICES LTD", "2026-10-08", role="corporate-director"),
        _officer("BROWN, Lee", "2026-10-08", role="secretary"),
        _officer("GREEN, Kim", "2012-03-03", occupation="Company Director"),
    ],
}


def _ch(routes: dict[str, Any], seen: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        body = routes.get(req.url.path)
        return httpx.Response(404, json={}) if body is None else httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def test_register_names_are_shown_forename_first() -> None:
    assert display_name("PATEL, Asha Rani") == "Asha Rani Patel"
    assert display_name("Single Name") == "Single Name"


def test_new_director_is_a_leadership_signal_without_the_persons_name() -> None:
    seen: list[httpx.Request] = []
    src = OfficerAppointmentSource(
        [WatchedCompany("Contoso Managed IT Ltd", "1234567", "contoso-it.example"), WatchedCompany("Bad", "!!")],
        "ch-key",
        transport=_ch({"/company/01234567/officers": OFFICERS}, seen),
    )
    [sig] = src.fetch("", SINCE)
    assert seen[0].headers["authorization"].startswith("Basic ")  # the free API key, as basic auth
    assert seen[0].url.params["order_by"] == "appointed_on"
    assert sig.signal_type == "leadership_change" and sig.company_number == "01234567"
    assert "Chief Technology Officer" in sig.text and "2026-10-06" in sig.text
    assert "Patel" not in sig.text and "Asha" not in sig.text and "Patel" not in sig.source_record_id
    assert sig.source_url.endswith("/company/01234567/officers")
    assert sig.domain == "contoso-it.example" and sig.country_hint == "GB"


def test_directors_as_decision_makers_have_no_email_and_minimal_data() -> None:
    ch = CompaniesHouseOfficers("ch-key", transport=_ch({"/company/01234567/officers": OFFICERS}))
    people = ch.find_people("", ["CTO"], "Contoso Managed IT Ltd", company_number="01234567")
    assert [(p.person_name, p.title) for p in people] == [
        ("Asha Patel", "Director (Chief Technology Officer)"),
        ("Sam Jones", "Managing Director"),
        ("Kim Green", "Director"),
    ]
    for p in people:
        assert p.professional_email is None and p.profile_url is None and p.location_text == "England"
        assert p.data_source == "companies_house:officers:01234567"
        assert "1980" not in repr(p) and "Private Road" not in repr(p) and "British" not in repr(p)


def test_decision_makers_by_name_need_one_exact_active_match() -> None:
    search_one = {
        "items": [
            {"title": "CONTOSO MANAGED IT LIMITED", "company_number": "01234567", "company_status": "active"},
            {"title": "CONTOSO MANAGED IT SERVICES LIMITED", "company_number": "07654321", "company_status": "active"},
        ]
    }
    ch = CompaniesHouseOfficers(
        "k", transport=_ch({"/search/companies": search_one, "/company/01234567/officers": OFFICERS})
    )
    assert len(ch.find_people("", ["CTO"], "Contoso Managed IT Ltd")) == 3
    two = {"items": [dict(search_one["items"][0]), dict(search_one["items"][0], company_number="09999999")]}
    ambiguous = CompaniesHouseOfficers("k", transport=_ch({"/search/companies": two}))
    assert ambiguous.find_people("", ["CTO"], "Contoso Managed IT Ltd") == []
    assert CompaniesHouseOfficers("k", transport=_ch({})).find_people("", ["CTO"]) == []


def test_unknown_company_number_gives_nobody() -> None:
    ch = CompaniesHouseOfficers("k", transport=_ch({}))
    assert ch.find_people("", ["CTO"], company_number="01234567") == []
    with pytest.raises(ValueError):
        CompaniesHouseOfficers("")


# ---------------------------------------------------------------- configuration and wiring


def _keys() -> dict[str, str]:
    return {
        n: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        for n in ("pii_master_key", "blind_index_key", "suppression_hash_key", "unsubscribe_signing_key")
    }


def _with_watchlist(**icp: Any) -> TenantConfig:
    data = DEMO_CONFIG.model_dump()
    data["icp"] |= icp
    data["watchlist"] = [
        {"name": "Contoso Managed IT Ltd", "company_number": "01234567", "job_board": "greenhouse:contoso"},
        {"name": "Fabrikam Ltd", "job_board": "lever:fabrikam"},
        {"name": "Northwind Ltd", "company_number": "SC123456"},
        {"name": "Plain Ltd"},
    ]
    return TenantConfig.model_validate(data)


def test_watchlist_is_validated_and_optional() -> None:
    assert DEMO_CONFIG.watchlist == []  # existing configurations stay valid
    data = DEMO_CONFIG.model_dump()
    for bad in ({"name": "X", "job_board": "workday:x"}, {"name": "X", "job_board": "greenhouse:../x"}, {"name": ""}):
        with pytest.raises(ValidationError):
            TenantConfig.model_validate(data | {"watchlist": [bad]})


def test_live_sources_add_the_watchlist_sources() -> None:
    cfg = _with_watchlist(hiring_keywords=["service desk", "-intern"])
    no_key = live_sources(cfg, Settings(**_keys()))
    assert [b.source.name for b in no_key] == ["contracts_finder.live", "find_a_tender.live", "job_boards.live"]
    assert no_key[2].query == "service desk; -intern"
    with_key = live_sources(cfg, Settings(**_keys(), companies_house_api_key="ch-key"))
    assert with_key[-1].source.name == "companies_house.officers"
    assert [w.company_number for w in with_key[-1].source._watched] == ["01234567", "SC123456"]  # type: ignore[attr-defined]
    assert [b.source.name for b in live_sources(DEMO_CONFIG)] == ["contracts_finder.live", "find_a_tender.live"]


def test_free_only_never_uses_the_paid_ai() -> None:
    prod = Settings(
        **_keys(),
        environment="prod",
        cookie_secure=True,
        global_go_live=True,
        llm_adapter_mode="live",
        anthropic_api_key="sk-test",
        database_url="postgresql+psycopg://u:p@h/db",
    )
    assert isinstance(select_backend(prod, tenant_live=True, golive_complete=True), AnthropicBackend)
    free = prod.model_copy(update={"free_only": True})
    assert isinstance(select_backend(free, tenant_live=True, golive_complete=True), OllamaBackend)
    # the three-key rule still applies: not all keys on -> the simulator, not even the local model
    assert not isinstance(select_backend(free, tenant_live=False, golive_complete=True), OllamaBackend)
