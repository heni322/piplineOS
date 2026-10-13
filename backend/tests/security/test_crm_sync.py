"""The client's CRM. Writing to it is a side effect: nothing reaches the CRM until the adapter mode, the client's live
flag AND global go-live (with the signed checklist) are all on; until then each would-be write is recorded,
encrypted, in the dry-run log. Sandbox mode never writes either (the connected CRM is the client's real one).
A person who objected is never sent to the CRM. The access token is write-only. Attendance the client records in
the CRM counts as billing evidence."""

from __future__ import annotations

from datetime import timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.adapters.crm.mock import MockCrm
from app.db.session import DbContext, transaction
from app.demo.conversations import run_conversations
from app.demo.seed import DEMO_CRM_STAGES, providers, seed_demo
from app.domain.outbound import crm
from app.domain.suppression.service import RecipientHashes, suppress_email
from app.workflows.pipeline import agent_context, run_campaign

pytestmark = pytest.mark.db


@pytest.fixture
def booked(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    """Demo client with a connected (fictional) HubSpot and one confirmed meeting that hasn't happened yet."""
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    run_campaign(ctx, providers())
    run = run_conversations(ctx, providers(), demo.operator_id, attend=False)
    assert run.meeting_id is not None
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        m = conn.execute(
            text("SELECT status, starts_at, ends_at FROM meetings WHERE id = :m"), {"m": run.meeting_id}
        ).one()
    assert m.status == "confirmed"
    return demo, run.meeting_id, m


def _rows(engine, tenant_id):  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(tenant_id), engine) as conn:
        return conn.execute(
            text(
                "SELECT idempotency_key, payload_enc FROM dry_run_log WHERE effect_type = 'crm_write' "
                "ORDER BY created_at"
            )
        ).all()


def _all_keys_on(engine, demo, settings):  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator", user_id=demo.operator_id), engine) as conn:
        conn.execute(text("UPDATE tenants SET live_mode = true WHERE id = :t"), {"t": demo.tenant_id})
        for item in conn.execute(text("SELECT id FROM golive_checklist_items")).scalars():
            conn.execute(text("SELECT golive_sign_off(:i, 'https://evidence.example')"), {"i": item})
    return settings.model_copy(update={"crm_adapter_mode": "live", "global_go_live": True})


def test_crm_writes_wait_in_the_dry_run_log_until_all_three_keys_are_on(booked, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo, meeting_id, m = booked
    fake = MockCrm()
    now = m.starts_at - timedelta(hours=1)
    for mode in ("live", "sandbox"):  # key 1 only; and sandbox, which never writes to a client's real CRM
        s = settings.model_copy(update={"crm_adapter_mode": mode})
        r = crm.sync_tenant(clean_db, s, cipher, demo.tenant_id, now=now, live_adapter=fake)
        assert r.mode == "dry_run"
    assert fake.calls == []
    rows = _rows(clean_db, demo.tenant_id)
    assert len(rows) == 1  # recorded once, not once per run
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        payload = cipher.decrypt(conn, demo.tenant_id, "dry_run_log.payload", rows[0].payload_enc)
        link = conn.execute(text("SELECT * FROM crm_links WHERE meeting_id = :m"), {"m": meeting_id}).mappings().one()
    assert "qualifiedtobuy" in payload and "Contoso Managed IT Ltd" in payload  # the confirmed stage, would-be deal
    assert b"Contoso" not in bytes(rows[0].payload_enc)  # encrypted at rest
    assert link["synced_mode"] == "dry_run" and link["state"] == "ok" and link["deal_ext_id"] is None
    assert link["last_error"] is None and link["contact_ext_id"] is None  # sync records hold ids only

    with transaction(DbContext(role="operator", user_id=demo.operator_id), clean_db) as conn:
        conn.execute(text("UPDATE meetings SET status = 'cancelled' WHERE id = :m"), {"m": meeting_id})
    crm.sync_tenant(clean_db, settings, cipher, demo.tenant_id, now=now, live_adapter=fake)
    assert len(_rows(clean_db, demo.tenant_id)) == 2 and fake.calls == []  # the change is recorded too


def test_live_write_and_attendance_read_back_only_with_all_three_keys(booked, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo, meeting_id, m = booked
    all_on = _all_keys_on(clean_db, demo, settings)
    fake = MockCrm()
    r = crm.sync_tenant(
        clean_db, all_on, cipher, demo.tenant_id, now=m.starts_at - timedelta(hours=1), live_adapter=fake
    )
    assert r.mode == "live" and r.written == 1 and fake.calls == [crm.meeting_ref(meeting_id)]
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        deal = conn.execute(text("SELECT deal_ext_id FROM crm_links WHERE meeting_id = :m"), {"m": meeting_id}).scalar()
        audit = conn.execute(text("SELECT count(*) FROM audit_log WHERE action = 'crm.synced'")).scalar()
    assert deal and audit == 1 and fake.deals[deal]["stage"] == DEMO_CRM_STAGES["confirmed"]
    assert crm.sync_tenant(clean_db, all_on, cipher, demo.tenant_id, now=m.starts_at, live_adapter=fake).written == 0

    after = m.ends_at + timedelta(hours=1)
    assert crm.sync_tenant(clean_db, all_on, cipher, demo.tenant_id, now=after, live_adapter=fake).attendance == 0
    fake.deals[deal]["stage"] = DEMO_CRM_STAGES["attended"]  # the client moves the deal on in their CRM
    r = crm.sync_tenant(clean_db, all_on, cipher, demo.tenant_id, now=after, live_adapter=fake)
    assert r.attendance == 1
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        ev = conn.execute(
            text("SELECT source, status, evidence FROM attendance_events WHERE meeting_id = :m"), {"m": meeting_id}
        ).one()
        status = conn.execute(text("SELECT status FROM meetings WHERE id = :m"), {"m": meeting_id}).scalar()
    assert (ev.source, ev.status, status) == ("crm", "attended", "attended")
    assert ev.evidence["deal_id"] == deal and ev.evidence["stage"] == DEMO_CRM_STAGES["attended"]


def test_a_person_who_objected_is_never_sent_to_the_crm(booked, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo, meeting_id, m = booked
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        enc = conn.execute(
            text(
                "SELECT p.email_enc FROM meetings mt JOIN leads l ON l.id = mt.lead_id JOIN prospects p "
                "ON p.id = l.prospect_id WHERE mt.id = :m"
            ),
            {"m": meeting_id},
        ).scalar()
        email = cipher.decrypt(conn, demo.tenant_id, "prospects.email", enc)
        suppress_email(conn, RecipientHashes.for_email(settings.key("suppression_hash_key"), email), "objection")
    all_on = _all_keys_on(clean_db, demo, settings)
    fake = MockCrm()
    r = crm.sync_tenant(
        clean_db, all_on, cipher, demo.tenant_id, now=m.starts_at - timedelta(hours=1), live_adapter=fake
    )
    assert r.skipped == 1 and r.written == 0 and fake.calls == []  # no deal yet, and no personal data may go
    dry = crm.sync_tenant(clean_db, settings, cipher, demo.tenant_id, now=m.starts_at, live_adapter=fake)
    assert dry.mode == "dry_run"
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        payloads = [
            cipher.decrypt(conn, demo.tenant_id, "dry_run_log.payload", p)
            for (p,) in conn.execute(text("SELECT payload_enc FROM dry_run_log WHERE effect_type = 'crm_write'"))
        ]
    assert payloads and all(email not in p for p in payloads)


def test_failures_are_recorded_without_provider_text_and_retried_a_bounded_number_of_times(
    booked, clean_db, settings, cipher
) -> None:  # type: ignore[no-untyped-def]
    from app.adapters.http import ProviderError

    demo, meeting_id, m = booked
    all_on = _all_keys_on(clean_db, demo, settings)

    class Broken(MockCrm):
        def sync(self, record, known):  # type: ignore[no-untyped-def]
            self.calls.append(record.ref)
            raise ProviderError(400, f"bad request for {record.contact.email if record.contact else ''}")

    broken = Broken()
    for _ in range(crm.MAX_ATTEMPTS + 2):
        crm.sync_tenant(
            clean_db, all_on, cipher, demo.tenant_id, now=m.starts_at - timedelta(hours=1), live_adapter=broken
        )
    assert len(broken.calls) == crm.MAX_ATTEMPTS  # then it waits for a person
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        link = conn.execute(
            text("SELECT state, attempts, last_error FROM crm_links WHERE meeting_id = :m"), {"m": meeting_id}
        ).one()
    assert (link.state, link.attempts, link.last_error) == ("error", crm.MAX_ATTEMPTS, "provider error 400")


# ------------------------------------------------------------------------------------------------------------ API
def _login(c: TestClient, email: str, password: str, totp: str | None = None) -> TestClient:
    r = c.post(
        "/api/v1/auth/login", json={"email": email, "password": password} | ({"totp_code": totp} if totp else {})
    )
    assert r.status_code == 200, r.text
    c.headers["x-csrf-token"] = c.cookies["pos_csrf"]
    return c


def test_client_connects_their_own_crm_and_the_token_is_never_returned(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    app = create_app()
    client = _login(TestClient(app), demo.client_email, demo.client_password or "")
    body = {
        "provider": "hubspot",
        "token": "pat-eu1-0000-real-looking-token",
        "pipeline_id": "default",
        "stages": {**DEMO_CRM_STAGES, "attended": "contractsent"},
    }
    r = client.put("/api/v1/crm/connection", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["connected"] and out["stages"]["attended"] == "contractsent"
    assert "token" not in r.text and "real-looking" not in r.text
    assert "real-looking" not in client.get("/api/v1/crm/connection").text
    with transaction(DbContext(role="operator"), clean_db) as conn:
        enc = conn.execute(
            text("SELECT token_enc FROM crm_connections WHERE tenant_id = :t"), {"t": demo.tenant_id}
        ).scalar()
        audit = conn.execute(
            text("SELECT after::text FROM audit_log WHERE action = 'crm.connected' ORDER BY created_at DESC LIMIT 1")
        ).scalar()
        assert cipher.decrypt(conn, demo.tenant_id, "crm_connections.token", enc) == body["token"]
    assert b"real-looking" not in bytes(enc) and "real-looking" not in (audit or "")

    bad = client.put("/api/v1/crm/connection", json={**body, "token": "has spaces in it!!"})
    assert bad.status_code == 422
    assert client.post("/api/v1/crm/connection/disconnect", json={"reason": "short"}).status_code == 422
    r = client.post("/api/v1/crm/connection/disconnect", json={"reason": "moving to another CRM next month"})
    assert r.status_code == 200 and r.json()["connected"] is False and r.json()["status"] == "disconnected"
    assert client.post("/api/v1/crm/sync/run?tenant_id=" + str(demo.tenant_id)).status_code == 403  # operator only

    op = _login(
        TestClient(app),
        "op@pipelineos.example",
        demo.operator_password or "",
        pyotp.TOTP(demo.operator_totp_secret or "").now(),
    )
    st = op.get(f"/api/v1/crm/connection?tenant_id={demo.tenant_id}").json()
    assert st["status"] == "disconnected" and st["disconnected_reason"].startswith("moving")


def test_another_client_cannot_see_or_change_the_connection(booked, clean_db) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import ProgrammingError

    demo, meeting_id, _ = booked
    with transaction(DbContext(role="operator"), clean_db) as conn:
        other = conn.execute(
            text("""INSERT INTO tenants (slug, legal_name, status, role) VALUES ('other-co', 'Other Co Ltd', 'active',
                    'processor_client') RETURNING id""")
        ).scalar_one()
    with transaction(DbContext(tenant_id=other, role="tenant_admin"), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM crm_connections")).scalar() == 0
        hijack = text("UPDATE crm_connections SET pipeline_id = 'hijack' WHERE tenant_id = :t")
        assert conn.execute(hijack, {"t": demo.tenant_id}).rowcount == 0
    with transaction(DbContext(tenant_id=demo.tenant_id, role="tenant_viewer"), clean_db) as conn:  # read only
        assert conn.execute(text("SELECT count(*) FROM crm_connections")).scalar() == 1
        assert conn.execute(text("UPDATE crm_connections SET pipeline_id = 'x'")).rowcount == 0
    admin = DbContext(tenant_id=demo.tenant_id, role="tenant_admin")
    # sync records: only the system writes them
    with pytest.raises(ProgrammingError, match="row-level security"), transaction(admin, clean_db) as conn:
        conn.execute(
            text("INSERT INTO crm_links (tenant_id, meeting_id, provider) VALUES (:t, :m, 'hubspot')"),
            {"t": demo.tenant_id, "m": meeting_id},
        )
    # nothing is ever deleted
    with pytest.raises(ProgrammingError), transaction(DbContext(role="operator"), clean_db) as conn:
        conn.execute(text("DELETE FROM crm_connections"))
