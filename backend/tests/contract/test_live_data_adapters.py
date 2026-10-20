"""Contract tests for the live data adapters: Contracts Finder / Find a Tender award notices (OCDS), Hunter.io
enrichment and verification, and the Companies House lookup by number. Every request goes to an in-process
httpx.MockTransport; the fixtures are synthetic (fictional companies, .example domains) in the providers' real shape."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.adapters.base import RateLimited
from app.adapters.companies_house.live import CompaniesHouseRegistry
from app.adapters.hunter import CreditsExhausted, HunterEnrichment, HunterVerifier
from app.adapters.signals.ocds import contracts_finder, find_a_tender, normalise_company_number, parse_query
from app.adapters.verifier.base import PersonObjected

SINCE = datetime(2026, 10, 11, 9, 30, tzinfo=UTC)
QUERY = "IT support; managed service; -catering"


def _party(pid: str, name: str, roles: list[str], scheme: str | None = None, ident: str | None = None, **addr: str):  # type: ignore[no-untyped-def]
    identifier: dict[str, Any] = {"legalName": name}
    if scheme:
        identifier |= {"scheme": scheme, "id": ident}
    return {
        "id": pid,
        "name": name,
        "identifier": identifier,
        "address": addr,
        "contactPoint": {"name": "Jo Private", "email": "jo.private@supplier.example", "telephone": "01632 960000"},
        "roles": roles,
    }


def release(ocid: str, rid: str, title: str, suppliers: list[dict[str, Any]], *, tag: str = "award", **extra: Any):  # type: ignore[no-untyped-def]
    return {
        "ocid": ocid,
        "id": rid,
        "date": "2026-10-12T14:00:00+01:00",
        "tag": [tag],
        "tender": {
            "title": title,
            "description": "Provision of services to the council.",
            "classification": {"scheme": "CPV", "id": "72000000", "description": "IT services"},
        },
        "buyer": {"id": "B1", "name": "Northshire County Council"},
        "parties": [_party("B1", "Northshire County Council", ["buyer"], countryName="England"), *suppliers],
        "awards": [
            {
                "id": "A1",
                "datePublished": "2026-10-12T15:00:00+01:00",
                "value": {"amount": 125000, "currency": "GBP"},
                "suppliers": [{"id": s["id"], "name": s["name"]} for s in suppliers],
            }
        ],
        **extra,
    }


CONTOSO = _party("GB-COH-1234567", "Contoso Managed IT Ltd", ["supplier"], "GB-COH", "1234567", countryName="England")
FABRIKAM = _party("GB-CFS-339117", "Fabrikam Cloud Ltd", ["supplier"], region="Scotland")


class Pages:
    def __init__(self, pages: dict[str, Any]) -> None:
        self.pages, self.calls = pages, []  # type: ignore[var-annotated]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        key = req.url.params.get("cursor", "first")
        if key == "limited":
            return httpx.Response(429, headers={"Retry-After": "30"})
        return httpx.Response(200, json=self.pages[key])


def test_contracts_finder_award_notices_become_tender_signals() -> None:
    nxt = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?stages=award&limit=100&cursor=p2"
    pages = Pages(
        {
            "first": {
                "releases": [
                    release("ocds-b5fd17-aaaa-1111", "r1", "IT support for schools", [CONTOSO]),
                    release("ocds-b5fd17-bbbb-2222", "r2", "Catering and IT support", [FABRIKAM]),  # excluded word
                    release("ocds-b5fd17-cccc-3333", "r3", "Road resurfacing", [FABRIKAM]),  # no keyword
                    release("ocds-b5fd17-dddd-4444", "r4", "IT support", [FABRIKAM], tag="tender"),  # not an award
                ],
                "links": {"next": nxt},
            },
            "p2": {
                "releases": [release("ocds-b5fd17-eeee-5555", "r5", "Managed service desk", [FABRIKAM])],
                "links": {},
            },
        }
    )
    src = contracts_finder(transport=httpx.MockTransport(pages))
    out = src.fetch(QUERY, SINCE)

    first = pages.calls[0].url
    assert first.path == "/Published/Notices/OCDS/Search"
    assert first.params["publishedFrom"] == "2026-10-11T09:30:00" and first.params["stages"] == "award"
    assert pages.calls[1].url.params["cursor"] == "p2"  # followed the publisher's own next link
    assert [s.company_name for s in out] == ["Contoso Managed IT Ltd", "Fabrikam Cloud Ltd"]
    c, f = out
    assert c.signal_type == "tender" and c.domain is None and c.company_number == "01234567"
    assert c.country_hint == "England" and f.country_hint == "Scotland" and f.company_number is None
    assert c.source_url == "https://www.contractsfinder.service.gov.uk/Notice/aaaa-1111"
    assert c.detected_at == datetime(2026, 10, 12, 14, 0, tzinfo=UTC)
    assert c.title == "Awarded: IT support for schools" and "GBP 125,000" in c.text
    assert c.source_record_id == "ocds-b5fd17-aaaa-1111:GB-COH-1234567"
    # notice contact details are never read
    assert all("Jo Private" not in s.text and "supplier.example" not in s.text and "01632" not in s.text for s in out)


def test_find_a_tender_uses_its_own_path_and_notice_links() -> None:
    rel = release("ocds-h6vhtk-0786eb", "096722-2026", "Managed service for networks", [CONTOSO])
    del rel["awards"][0]["datePublished"]  # Find a Tender awards often carry no dates: the notice date is used
    pages = Pages({"first": {"releases": [rel], "links": {}}})
    out = find_a_tender(transport=httpx.MockTransport(pages)).fetch(QUERY, SINCE)
    assert pages.calls[0].url.path == "/api/1.0/ocdsReleasePackages"
    assert pages.calls[0].url.params["updatedFrom"] == "2026-10-11T09:30:00"
    assert out[0].source_url == "https://www.find-tender.service.gov.uk/Notice/096722-2026"
    assert out[0].detected_at == datetime(2026, 10, 12, 13, 0, tzinfo=UTC)


def test_never_follows_a_next_link_to_another_site_and_maps_rate_limits() -> None:
    pages = Pages(
        {
            "first": {
                "releases": [release("ocds-b5fd17-a", "r1", "IT support", [CONTOSO])],
                "links": {"next": "https://evil.example/steal?cursor=p2"},
            }
        }
    )
    out = contracts_finder(transport=httpx.MockTransport(pages)).fetch(QUERY, SINCE)
    assert len(pages.calls) == 1 and len(out) == 1
    src = contracts_finder(transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "9"})))
    with pytest.raises(RateLimited):
        src.fetch(QUERY, SINCE)


def test_query_and_company_number_parsing() -> None:
    assert parse_query("IT support; ;-catering; Managed Service") == (["it support", "managed service"], ["catering"])
    assert normalise_company_number("1234567") == "01234567"
    assert normalise_company_number("sc 12345") == "SC012345"
    assert normalise_company_number("GB-CFS-339117") is None
    assert normalise_company_number("") is None


# ------------------------------------------------------------------------------------------------------- Hunter
DOMAIN_SEARCH = {
    "data": {
        "domain": "contoso-it.example",
        "organization": "Contoso Managed IT",
        "emails": [
            {
                "value": "dana.reyes@contoso-it.example",
                "type": "personal",
                "confidence": 94,
                "first_name": "Dana",
                "last_name": "Reyes",
                "position": "Head of Service Delivery",
                "seniority": "senior",
                "linkedin": "dana-reyes-example",
                "sources": [{"uri": "https://contoso-it.example/team"}],
            },
            {
                "value": "chris.lee@contoso-it.example",
                "confidence": 88,
                "first_name": "Chris",
                "last_name": "Lee",
                "position": "Managing Director",
                "seniority": "executive",
                "sources": [],
            },
            {  # objected at source (451 on lookup): never returned
                "value": "pat.objector@contoso-it.example",
                "confidence": 99,
                "first_name": "Pat",
                "last_name": "Objector",
                "position": "Head of Service Delivery",
                "seniority": "senior",
            },
            {
                "value": "j.junior@contoso-it.example",
                "confidence": 80,
                "first_name": "J",
                "last_name": "Junior",
                "position": "Engineer",
                "seniority": "junior",
            },
            {"value": "info@contoso-it.example", "confidence": 99, "position": None},
        ],
    }
}


def hunter_routes(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/v2/domain-search":
        return httpx.Response(200, json=DOMAIN_SEARCH)
    if req.url.path == "/v2/people/find":
        email = req.url.params["email"]
        if email.startswith("pat."):
            return httpx.Response(451, json={"errors": [{"id": "claimed_email"}]})
        if email.startswith("dana."):
            return httpx.Response(
                200, json={"data": {"location": "Leeds, England, United Kingdom", "geo": {"countryCode": "GB"}}}
            )
        return httpx.Response(404, json={"errors": [{"id": "not_found"}]})
    return httpx.Response(404)


def test_hunter_finds_people_by_company_name_with_location_and_no_profile_urls() -> None:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return hunter_routes(req)

    h = HunterEnrichment("secret-key", transport=httpx.MockTransport(handler))
    people = h.find_people("", ["Head of Service Delivery", "IT Director"], "Contoso Managed IT Ltd")
    search = calls[0]
    assert search.url.params["company"] == "Contoso Managed IT Ltd" and "domain" not in search.url.params
    assert search.url.params["limit"] == "10" and search.url.params["type"] == "personal"
    assert all(c.headers["X-API-KEY"] == "secret-key" and "secret-key" not in str(c.url) for c in calls)
    assert [p.person_name for p in people] == ["Dana Reyes", "Chris Lee"]  # objector skipped, junior filtered
    dana, chris = people
    assert dana.company_domain == "contoso-it.example" and dana.location_text == "Leeds, England, United Kingdom"
    assert dana.country == "GB" and dana.confidence == pytest.approx(0.94)
    assert dana.profile_url is None and chris.profile_url is None
    assert chris.location_text is None  # 404: unknown location -> the compliance engine decides (blocks unknown)
    assert dana.data_source.startswith("hunter.io domain-search contoso-it.example (1 public source")
    assert len([c for c in calls if c.url.path == "/v2/people/find"]) == 3  # at most MAX_LOCATED lookups


def test_hunter_errors_map_to_safe_outcomes() -> None:
    def status(code: int) -> HunterEnrichment:
        return HunterEnrichment("k", transport=httpx.MockTransport(lambda r: httpx.Response(code, json={})))

    with pytest.raises(RateLimited):
        status(403).find_people("contoso-it.example", ["CTO"])  # Hunter's per-second limit
    with pytest.raises(CreditsExhausted):
        status(429).find_people("contoso-it.example", ["CTO"])  # monthly credits used up: not retried
    assert status(451).find_people("contoso-it.example", ["CTO"]) == []
    none = HunterEnrichment(
        "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": {"domain": None}}))
    )
    assert none.find_people("", ["CTO"], "Unknown Co") == []
    assert none.find_people("", ["CTO"]) == []  # neither domain nor name: no call at all


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("valid", "valid"),
        ("invalid", "invalid"),
        ("disposable", "invalid"),
        ("accept_all", "risky"),
        ("webmail", "risky"),
        ("unknown", "unknown"),
        ("something-new", "unknown"),
    ],
)
def test_hunter_verifier_maps_statuses(status: str, expected: str) -> None:
    v = HunterVerifier(
        "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": {"status": status}}))
    )
    assert v.verify("dana.reyes@contoso-it.example") == expected


def test_hunter_verifier_objection_and_pending() -> None:
    objected = HunterVerifier("k", transport=httpx.MockTransport(lambda r: httpx.Response(451, json={})))
    with pytest.raises(PersonObjected) as exc:
        objected.verify("pat.objector@contoso-it.example")
    assert exc.value.email == "pat.objector@contoso-it.example"
    pending = HunterVerifier("k", transport=httpx.MockTransport(lambda r: httpx.Response(202, json={})))
    assert pending.verify("dana.reyes@contoso-it.example") == "unknown"


# ------------------------------------------------------------------------------------------- Companies House
def test_companies_house_lookup_by_number_is_exact() -> None:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path == "/company/01234567":
            return httpx.Response(
                200,
                json={
                    "company_name": "CONTOSO MANAGED IT LTD",
                    "type": "ltd",
                    "company_status": "active",
                    "jurisdiction": "england-wales",
                },
            )
        return httpx.Response(404, json={})

    reg = CompaniesHouseRegistry("k", transport=httpx.MockTransport(handler))
    found = reg.lookup("Contoso Managed IT Ltd", None, "01234567")
    assert found is not None and found.legal_form == "ltd" and found.registered_country == "GB"
    assert [c.url.path for c in calls] == ["/company/01234567"]  # no fuzzy name search when the number is known
    assert reg.lookup("Gone Ltd", None, "09999999") is None


def test_supplier_website_from_the_notice_becomes_the_domain_hint() -> None:
    from app.adapters.signals.ocds import party_domain

    assert party_domain({"details": {"url": "https://www.wtl.uk/"}}) == "wtl.uk"
    assert party_domain({"contactPoint": {"url": "lakeside-net.example"}}) == "lakeside-net.example"
    assert party_domain({"details": {"url": "not a url"}}) is None
    assert party_domain({"details": {"url": "javascript:alert(1)"}}) is None
    assert party_domain({"name": "No Website Ltd"}) is None
