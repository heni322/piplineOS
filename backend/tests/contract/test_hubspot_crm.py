"""Contract tests for the HubSpot CRM adapter. Every request goes to an in-process httpx.MockTransport (synthetic
data, .example domains); nothing touches the network."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.adapters.base import RateLimited
from app.adapters.crm.base import CrmContact, CrmIds, CrmMeetingRecord
from app.adapters.crm.hubspot import HubSpotCrm

AT = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


def record(contact: bool = True, stage: str = "appointmentscheduled") -> CrmMeetingRecord:
    return CrmMeetingRecord(
        ref="PO1A2B3C4D5E",
        deal_name="Introductory call - Contoso Managed IT Ltd [PO1A2B3C4D5E]",
        stage_id=stage,
        pipeline_id="default",
        starts_at=AT,
        company_name="Contoso Managed IT Ltd",
        company_domain="contoso-it.example",
        contact=CrmContact("dana.reyes@contoso-it.example", "Dana", "Reyes", "Head of Service Delivery")
        if contact
        else None,
    )


class Fake:
    def __init__(self, found_deal: str | None = None, found_company: str | None = None) -> None:
        self.calls: list[httpx.Request] = []
        self.found_deal, self.found_company = found_deal, found_company

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        p = req.url.path
        body: dict[str, Any] = json.loads(req.content) if req.content else {}
        if p == "/crm/v3/objects/deals/search":
            return httpx.Response(200, json={"results": [{"id": self.found_deal}] if self.found_deal else []})
        if p == "/crm/v3/objects/companies/search":
            return httpx.Response(200, json={"results": [{"id": self.found_company}] if self.found_company else []})
        if p == "/crm/v3/objects/companies":
            return httpx.Response(201, json={"id": "c-100"})
        if p == "/crm/v3/objects/contacts/batch/upsert":
            return httpx.Response(200, json={"results": [{"id": "p-200"}]})
        if p.startswith("/crm/v4/objects/contact/"):
            return httpx.Response(200, json={})
        if p == "/crm/v3/objects/deals" and req.method == "POST":
            assert body["properties"]["dealname"].endswith("[PO1A2B3C4D5E]")
            return httpx.Response(201, json={"id": "d-300"})
        if p.startswith("/crm/v3/objects/deals/") and req.method == "PATCH":
            return httpx.Response(200, json={"id": p.rsplit("/", 1)[1]})
        if p == "/crm/v3/objects/deals/d-300" and req.method == "GET":
            return httpx.Response(200, json={"id": "d-300", "properties": {"dealstage": "presentationscheduled"}})
        return httpx.Response(404, json={})


def crm(fake: Fake) -> HubSpotCrm:
    return HubSpotCrm("pat-na1-secret-token", transport=httpx.MockTransport(fake))


def test_first_sync_creates_company_contact_and_associated_deal() -> None:
    fake = Fake()
    ids = crm(fake).sync(record(), CrmIds())
    assert (ids.company_id, ids.contact_id, ids.deal_id) == ("c-100", "p-200", "d-300")
    assert ids.created == ["company", "deal"]
    assert all(c.headers["Authorization"] == "Bearer pat-na1-secret-token" for c in fake.calls)
    assert all("secret-token" not in str(c.url) for c in fake.calls)
    paths = [(c.method, c.url.path) for c in fake.calls]
    assert paths == [
        ("POST", "/crm/v3/objects/deals/search"),  # a retry would find the deal by its reference first
        ("POST", "/crm/v3/objects/companies/search"),
        ("POST", "/crm/v3/objects/companies"),
        ("POST", "/crm/v3/objects/contacts/batch/upsert"),
        ("PUT", "/crm/v4/objects/contact/p-200/associations/default/company/c-100"),
        ("POST", "/crm/v3/objects/deals"),
    ]
    search = json.loads(fake.calls[0].content)["filterGroups"][0]["filters"][0]
    assert search == {"propertyName": "dealname", "operator": "CONTAINS_TOKEN", "value": "PO1A2B3C4D5E"}
    upsert = json.loads(fake.calls[3].content)["inputs"][0]
    assert upsert["idProperty"] == "email" and upsert["id"] == "dana.reyes@contoso-it.example"
    assert upsert["properties"] == {
        "email": "dana.reyes@contoso-it.example",
        "firstname": "Dana",
        "lastname": "Reyes",
        "jobtitle": "Head of Service Delivery",
    }
    deal = json.loads(fake.calls[5].content)
    assert deal["properties"]["pipeline"] == "default" and deal["properties"]["dealstage"] == "appointmentscheduled"
    assert {(a["to"]["id"], a["types"][0]["associationTypeId"]) for a in deal["associations"]} == {
        ("p-200", 3),
        ("c-100", 5),
    }


def test_retry_finds_the_deal_by_reference_and_never_creates_a_second() -> None:
    fake = Fake(found_deal="d-999", found_company="c-7")
    ids = crm(fake).sync(record(stage="qualifiedtobuy"), CrmIds())
    assert ids.deal_id == "d-999" and ids.company_id == "c-7" and ids.created == []
    assert not any(
        c.method == "POST" and c.url.path in ("/crm/v3/objects/deals", "/crm/v3/objects/companies") for c in fake.calls
    )
    patch = next(c for c in fake.calls if c.method == "PATCH")
    assert patch.url.path == "/crm/v3/objects/deals/d-999"
    assert json.loads(patch.content)["properties"]["dealstage"] == "qualifiedtobuy"


def test_known_ids_only_move_the_deal_stage() -> None:
    fake = Fake()
    ids = crm(fake).sync(record(stage="closedlost"), CrmIds("c-1", "p-2", "d-3"))
    assert ids.deal_id == "d-3"
    assert [c.url.path for c in fake.calls if c.method in ("POST", "PATCH") and "batch" not in c.url.path] == [
        "/crm/v3/objects/deals/d-3"
    ]


def test_without_personal_data_only_an_existing_deal_stage_changes() -> None:
    fake = Fake(found_deal="d-5")
    ids = crm(fake).sync(record(contact=False, stage="closedlost"), CrmIds())
    assert ids.deal_id == "d-5" and ids.contact_id is None
    assert [c.url.path for c in fake.calls] == ["/crm/v3/objects/deals/search", "/crm/v3/objects/deals/d-5"]
    nothing = Fake()
    assert crm(nothing).sync(record(contact=False), CrmIds()).deal_id is None
    assert [c.url.path for c in nothing.calls] == ["/crm/v3/objects/deals/search"]  # no company, no contact, no deal


def test_deal_stage_read_back_and_errors() -> None:
    fake = Fake()
    assert crm(fake).deal_stage("d-300") == "presentationscheduled"
    assert crm(fake).deal_stage("gone") is None
    limited = HubSpotCrm("pat-na1-x123456789", transport=httpx.MockTransport(lambda r: httpx.Response(429, json={})))
    with pytest.raises(RateLimited):
        limited.sync(record(), CrmIds())
    with pytest.raises(ValueError):
        HubSpotCrm("")
