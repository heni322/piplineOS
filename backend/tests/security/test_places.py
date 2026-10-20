"""Google Places (paid): nothing reaches Google unless the gate is live (three keys), never with FREE_ONLY, never in
sandbox; every billed request is counted under the monthly cap first; nothing Google returns is stored; and a
company found by a search is kept only when its own site prints a number the register confirms. In-process
transports only."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.adapters.base import AdapterNotConfigured
from app.adapters.companies_house.base import RegistryCompany
from app.adapters.google_places import FIELDS, GooglePlaces, website_domain
from app.adapters.website_contacts import WebsiteFinder, printed_number
from app.db.session import DbContext, transaction
from app.demo.seed import seed_demo
from app.domain.paid.discovery import discover_companies
from app.domain.paid.gate import PaidGate
from app.domain.paid.places import PROVIDER, CapReached, GatedPlaces, reserve_call, usage
from app.domain.paid.providers import places_for

pytestmark = pytest.mark.db


def places_api(results: list[dict[str, Any]], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"places": results})

    return httpx.MockTransport(handler)


HITS = [
    {"id": "p1", "displayName": {"text": "Lakeside Networks"}, "formattedAddress": "1 High St, Leeds",
     "websiteUri": "https://www.lakesidenetworks.co.uk/?utm=maps", "businessStatus": "OPERATIONAL"},
    {"id": "p2", "displayName": {"text": "Closed Old Firm"}, "websiteUri": "https://old.example",
     "businessStatus": "CLOSED_PERMANENTLY"},
    {"id": "p3", "displayName": {"text": "Unrelated Bakery"}, "websiteUri": "https://bakery.example"},
    {"id": "p4", "displayName": {"text": "No Site Ltd"}},
]  # fmt: skip


def gated(clean_db, settings, cipher, mode: str, seen: list[httpx.Request], cap: int = 5) -> GatedPlaces:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    gate = PaidGate(clean_db, cipher, settings.key("blind_index_key"), demo.tenant_id, mode)  # type: ignore[arg-type]
    return GatedPlaces(
        GooglePlaces("real-key", transport=places_api(HITS, seen)), gate, clean_db, cap=cap, region="GB", language="en"
    )


def test_request_shape_key_in_header_and_closed_places_dropped() -> None:
    seen: list[httpx.Request] = []
    hits, token = GooglePlaces("k", transport=places_api(HITS, seen)).search("IT support Leeds", region_code="GB")
    req = seen[0]
    assert req.url.path == "/v1/places:searchText" and "k" not in str(req.url)
    assert req.headers["X-Goog-Api-Key"] == "k" and req.headers["X-Goog-FieldMask"] == FIELDS
    assert "nationalPhoneNumber" not in FIELDS and "reviews" not in FIELDS  # data minimisation
    body = json.loads(req.content)
    assert body == {"textQuery": "IT support Leeds", "regionCode": "gb", "languageCode": "en", "pageSize": 20}
    assert [h.place_id for h in hits] == ["p1", "p3", "p4"] and token is None
    assert hits[0].website == "lakesidenetworks.co.uk" and hits[2].website is None
    assert website_domain("not a url") is None


def test_nothing_is_called_until_live_and_the_would_be_search_is_logged(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    for mode in ("dry_run", "sandbox"):
        seen: list[httpx.Request] = []
        p = gated(clean_db, settings, cipher, mode, seen)
        assert p.websites_for("01234567", "Lakeside Networks Ltd") == [] and p.discover("IT Leeds") == []
        assert seen == []
    with transaction(DbContext.system(p.gate.tenant_id), clean_db) as conn:
        rows = conn.execute(text("SELECT adapter FROM dry_run_log WHERE effect_type = 'paid_api_call'")).scalars()
        assert set(rows) == {PROVIDER}
    assert usage(clean_db) == 0


def test_live_calls_are_counted_under_the_monthly_cap(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []
    p = gated(clean_db, settings, cipher, "live", seen, cap=2)
    assert p.websites_for("01234567", "Lakeside Networks Ltd") == ["lakesidenetworks.co.uk", "bakery.example"]
    assert len(p.discover("IT support Leeds")) == 3
    assert p.discover("again") == [] and len(seen) == 2  # third request refused at the cap, never sent
    assert usage(clean_db) == 2
    with pytest.raises(CapReached):
        reserve_call(clean_db, "other.provider", 0)
    with transaction(DbContext.system(), clean_db) as conn:  # counts only: nothing from Google is stored
        assert conn.execute(text("SELECT calls FROM provider_usage")).scalars().all() == [2]


def test_places_is_never_built_free_only_or_without_a_key(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    with pytest.raises(AdapterNotConfigured, match="FREE_ONLY"):
        places_for(settings.model_copy(update={"free_only": True}), clean_db, cipher, demo.tenant_id)
    with pytest.raises(AdapterNotConfigured, match="GOOGLE_PLACES_API_KEY"):
        places_for(settings.model_copy(update={"free_only": False}), clean_db, cipher, demo.tenant_id)
    s = settings.model_copy(
        update={"free_only": False, "google_places_api_key": SecretStr("k"), "places_adapter_mode": "live"}
    )
    assert not places_for(s, clean_db, cipher, demo.tenant_id).allowed  # client not live, go-live not signed


SITE = "<footer>Lakeside Networks Ltd, registered in England and Wales, company no. 01234567.</footer>"
CONTACT = SITE + "<p>sales@lakesidenetworks.co.uk, dana.reyes@lakesidenetworks.co.uk</p>"


class Register:
    def __init__(self, companies: dict[str, RegistryCompany]) -> None:
        self.companies, self.name = companies, "fake"

    def lookup(self, company_name: str, domain=None, company_number=None, country=None):  # type: ignore[no-untyped-def]
        return self.companies.get(company_number or "")


def test_discovery_keeps_only_register_confirmed_companies_with_their_published_address(
    clean_db, settings, cipher
) -> None:  # type: ignore[no-untyped-def]
    pages = {
        "lakesidenetworks.co.uk/": SITE,
        "lakesidenetworks.co.uk/contact": CONTACT,
        "bakery.example/": "<p>Fresh bread. Company number 07654321</p>",  # number the register does not confirm
    }

    def handler(req: httpx.Request) -> httpx.Response:
        body = pages.get(f"{req.url.host}{req.url.path}")
        html = {"content-type": "text/html"}
        return httpx.Response(200, text=body, headers=html) if body else httpx.Response(404)

    finder = WebsiteFinder(transport=httpx.MockTransport(handler), public_host=lambda h: True, mx=lambda d: True)
    register = Register({
        "01234567": RegistryCompany("01234567", "LAKESIDE NETWORKS LIMITED", "ltd", "active", "GB"),
        "07654321": RegistryCompany("07654321", "SOMEONE ELSE HOLDINGS LIMITED", "ltd", "active", "GB"),
    })  # fmt: skip
    seen: list[httpx.Request] = []
    report = discover_companies(
        gated(clean_db, settings, cipher, "live", seen), finder, register, "IT support Leeds", country="GB"
    )
    assert (report.searched, report.with_website, report.not_confirmed_by_register) == (3, 2, 1)
    assert report.site_check_failed == 0 and report.no_number_on_site == 0, report
    (found,) = report.found
    assert (found.name, found.company_number, found.domain) == ("LAKESIDE NETWORKS LIMITED", "01234567",
                                                                 "lakesidenetworks.co.uk")  # fmt: skip
    assert found.address == "sales@lakesidenetworks.co.uk"  # generic only, never the person's address


def test_printed_numbers() -> None:
    assert printed_number("Registered in England No: 12345678", "GB") == "12345678"
    assert printed_number("SIRET : 123 456 789 00012", "FR") == "123456789"
    assert printed_number("RCS Lyon B 123 456 789", "FR") == "123456789"
    assert printed_number("Call 01234 567890", "GB") is None
