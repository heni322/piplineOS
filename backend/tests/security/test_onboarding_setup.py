"""Onboarding a brand-new client entirely from the console: offer and pricing from a template, sending domain,
inbox and campaign, with the client's signatures still required before activation."""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient

from tests.factories import make_operator
from tests.security.test_api import SECRET, login

pytestmark = pytest.mark.db

SETUP = {
    "niche": "uk-it-msp",
    "sender_name": "Sam Carter",
    "sender_title": "Director",
    "value_proposition": "fixed-price IT support for growing UK companies, with a named engineer",
    "outreach_purpose": "IT support for growing companies",
    "pricing": {"setup_fee_minor": 49_000, "per_meeting_minor": 25_000, "monthly_retainer_minor": 0},
}


@pytest.fixture
def op(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    make_operator(clean_db, settings, cipher, email="op@pipelineos.example", totp_secret=SECRET)
    c = TestClient(create_app())
    login(c, "op@pipelineos.example", "operator-password-123", pyotp.TOTP(SECRET).now())
    r = c.post(
        "/api/v1/clients",
        json={
            "slug": "acme-it",
            "legal_name": "Acme IT Ltd",
            "company_number": "01234567",
            "registered_address": "1 High St, Leeds",
            "billing_currency": "GBP",
        },
    )
    assert r.status_code == 201, r.text
    return c, r.json()["id"]


def test_new_client_gets_a_configuration_from_a_template(op) -> None:  # type: ignore[no-untyped-def]
    c, tid = op
    before = c.get(f"/api/v1/clients/{tid}/setup").json()
    assert before["configured"] is False and "uk-it-msp" in before["templates"] and before["criteria"]
    assert c.put(f"/api/v1/clients/{tid}/setup", json=SETUP | {"niche": None}).status_code == 422
    r = c.put(f"/api/v1/clients/{tid}/setup", json=SETUP)
    assert r.status_code == 200, r.text
    after = c.get(f"/api/v1/clients/{tid}/setup").json()
    assert after["configured"] and after["sender_name"] == "Sam Carter" and after["subject_variants"] == 1
    assert c.put(f"/api/v1/clients/{tid}/setup", json=SETUP | {"subject_variants": 2}).status_code == 200
    assert c.get(f"/api/v1/clients/{tid}/setup").json()["subject_variants"] == 2
    assert after["pricing"]["per_meeting_minor"] == 25_000 and after["currency"] == "GBP"
    items = {i["key"]: i["done"] for i in c.get("/api/v1/clients").json()[0]["checklist"]}
    assert items["config"] and items["pricing"] and not items["criteria_signed"]
    # Targeting now works on the new configuration.
    assert c.get(f"/api/v1/clients/{tid}/targeting").status_code == 200


def test_editing_criteria_keeps_unchanged_ones_and_needs_a_new_signature(op) -> None:  # type: ignore[no-untyped-def]
    c, tid = op
    c.put(f"/api/v1/clients/{tid}/setup", json=SETUP)
    crit = c.get(f"/api/v1/clients/{tid}/setup").json()["criteria"]
    new = [*crit[:2], {"question": "They have at least 20 staff", "required": False, "evidence_from": "reply"}]
    assert c.put(f"/api/v1/clients/{tid}/setup", json=SETUP | {"criteria": new}).status_code == 200
    got = c.get(f"/api/v1/clients/{tid}/setup").json()["criteria"]
    assert [x["question"] for x in got] == [x["question"] for x in new] and got[2]["required"] is False
    bad = c.put(f"/api/v1/clients/{tid}/setup", json=SETUP | {"pricing": {"per_meeting_minor": -1}})
    assert bad.status_code == 422


def test_domain_inbox_and_campaign_from_the_console(op) -> None:  # type: ignore[no-untyped-def]
    c, tid = op
    c.put(f"/api/v1/clients/{tid}/setup", json=SETUP)
    d = c.post(f"/api/v1/clients/{tid}/sending-domains", json={"domain": "Mail.Acme-IT.example"})
    assert d.status_code == 201, d.text
    assert d.json()["domain"] == "mail.acme-it.example" and d.json()["checked_at"] is None
    assert c.post(f"/api/v1/clients/{tid}/sending-domains", json={"domain": "not a domain"}).status_code == 422
    assert c.post(f"/api/v1/clients/{tid}/sending-domains", json={"domain": "mail.acme-it.example"}).status_code == 409
    i = c.post(
        f"/api/v1/clients/{tid}/inboxes",
        json={
            "sending_domain_id": d.json()["id"],
            "address": "sam@mail.acme-it.example",
            "display_name": "Sam Carter",
            "provider": "msgraph",
        },
    )
    assert i.status_code == 201, i.text
    assert (i.json()["status"], i.json()["daily_cap"]) == ("warming", 10)  # warm-up start cap
    wrong = c.post(
        f"/api/v1/clients/{tid}/inboxes",
        json={
            "sending_domain_id": d.json()["id"],
            "address": "sam@elsewhere.example",
            "display_name": "Sam",
            "provider": "msgraph",
        },
    )
    assert wrong.status_code == 422  # the address must be on the sending domain
    camp = c.post(f"/api/v1/clients/{tid}/campaigns", json={"name": "Pilot: contract winners"})
    assert camp.status_code == 201, camp.text
    assert camp.json()["status"] == "draft" and camp.json()["pilot_max_contacts"] == 100
    # A campaign can only start for an active client.
    assert c.post(f"/api/v1/campaigns/{camp.json()['id']}/start").status_code == 409


def test_a_new_client_goes_from_nothing_to_a_running_pilot(op, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    import uuid as _uuid

    from sqlalchemy import text

    from app.db.session import DbContext, transaction
    from tests.factories import make_tenant_user

    c, tid = op
    assert c.put(f"/api/v1/clients/{tid}/setup", json=SETUP).status_code == 200
    assert (
        c.post(f"/api/v1/clients/{tid}/lia", json={"reference": "LIA-ACME-1", "approved_on": "2026-10-01"}).status_code
        == 204
    )
    assert c.post(f"/api/v1/clients/{tid}/dpa", json={"version": "v1", "signed_on": "2026-10-02"}).status_code == 204
    _, email = make_tenant_user(clean_db, settings, cipher, _uuid.UUID(tid), email="admin@acme-it.example")
    client = TestClient(c.app)
    login(client, email, "tenant-password-123")
    for kind in ("qualification_criteria", "pricing"):
        assert client.post(f"/api/v1/terms/{kind}/sign").status_code == 201
    d = c.post(f"/api/v1/clients/{tid}/sending-domains", json={"domain": "mail.acme-it.example"}).json()
    with transaction(DbContext(role="operator"), clean_db) as conn:  # DNS checking itself is tested elsewhere
        conn.execute(
            text("UPDATE sending_domains SET spf_ok = true, dkim_ok = true, dmarc_ok = true WHERE id = :d"),
            {"d": d["id"]},
        )
    pending = [i["key"] for i in c.get("/api/v1/clients").json()[0]["checklist"] if not i["done"]]
    assert pending == []
    assert c.post(f"/api/v1/clients/{tid}/activate").json()["status"] == "active"
    camp = c.post(f"/api/v1/clients/{tid}/campaigns", json={"name": "Pilot"}).json()
    started = c.post(f"/api/v1/campaigns/{camp['id']}/start")
    assert started.status_code == 200 and started.json()["status"] == "active"
    assert c.post(f"/api/v1/campaigns/{camp['id']}/start").status_code == 409
    # Changing the price afterwards needs the client's acceptance again (billing blocks until then).
    c.put(f"/api/v1/clients/{tid}/setup", json=SETUP | {"pricing": {"per_meeting_minor": 30_000}})
    terms = {t["kind"]: t["signed"] for t in client.get("/api/v1/terms").json()}
    assert terms == {"qualification_criteria": True, "pricing": False}


def test_company_details_can_be_corrected_and_are_audited(op, clean_db) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    from app.db.session import DbContext, transaction

    c, tid = op
    r = c.put(
        f"/api/v1/clients/{tid}/details",
        json={
            "legal_name": "Acme IT Services Ltd",
            "company_number": "01234567",
            "registered_address": "3 New Rd, Leeds",
            "vat_number": " ",
        },
    )
    assert r.status_code == 200 and r.json()["legal_name"] == "Acme IT Services Ltd"
    assert c.put(f"/api/v1/clients/{tid}/details", json={"legal_name": "A"}).status_code == 422
    with transaction(DbContext(role="operator"), clean_db) as conn:
        a = conn.execute(text("SELECT before, after FROM audit_log WHERE action = 'tenant.details_changed'")).one()
    assert a.before["legal_name"] == "Acme IT Ltd" and a.after["registered_address"] == "3 New Rd, Leeds"
    assert a.after["vat_number"] is None
    # A field left out is kept, not erased.
    c.put(f"/api/v1/clients/{tid}/details", json={"legal_name": "Acme IT Services Ltd", "vat_number": "GB123456789"})
    c.put(f"/api/v1/clients/{tid}/details", json={"legal_name": "Acme IT Services Limited"})
    with transaction(DbContext(role="operator"), clean_db) as conn:
        t = conn.execute(text("SELECT vat_number, company_number FROM tenants WHERE id = :t"), {"t": tid}).one()
    assert (t.vat_number, t.company_number) == ("GB123456789", "01234567")


def test_pipelineos_selling_itself_as_controller(op, clean_db) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    from app.db.session import DbContext, transaction

    c, _ = op
    r = c.post(
        "/api/v1/own-account",
        json={
            "legal_name": "PipelineOS Ltd",
            "company_number": "12345678",
            "registered_address": "10 Example St, London",
        },
    )
    assert r.status_code == 201 and r.json()["own"] is True
    own = r.json()["id"]
    assert c.post("/api/v1/own-account", json={"legal_name": "Again Ltd"}).status_code == 409  # only one
    keys = {i["key"] for i in r.json()["checklist"]}
    assert {"lia", "config", "sending_domain", "company_details"} <= keys
    assert not keys & {"dpa", "client_user", "criteria_signed", "pricing_signed", "pricing"}  # we are the controller
    setup = SETUP | {
        "niche": "pipelineos-self",
        "value_proposition": "qualified first meetings for IT providers",
        "outreach_purpose": "new-business meetings for IT providers",
    }
    assert c.put(f"/api/v1/clients/{own}/setup", json=setup).status_code == 200
    assert (
        c.post(f"/api/v1/clients/{own}/lia", json={"reference": "LIA-OWN-1", "approved_on": "2026-10-01"}).status_code
        == 204
    )
    d = c.post(f"/api/v1/clients/{own}/sending-domains", json={"domain": "outreach.pipelineos.example"}).json()
    with transaction(DbContext(role="operator"), clean_db) as conn:
        conn.execute(
            text("UPDATE sending_domains SET spf_ok = true, dkim_ok = true, dmarc_ok = true WHERE id = :d"),
            {"d": d["id"]},
        )
    assert c.post(f"/api/v1/clients/{own}/activate").json()["status"] == "active"
    listed = c.get("/api/v1/clients").json()
    assert listed[-1]["id"] == own and listed[-1]["own"] and not listed[0]["own"]  # listed after the clients
