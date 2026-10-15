"""Phase 7 go-live preparation: sending-domain DNS verification (stored, shown, required by onboarding), mailbox
warm-up that only advances on days with REAL sends, and the read-only readiness list."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.demo.conversations import _approve_all
from app.demo.seed import providers, seed_demo
from app.domain.deliverability import warmup
from app.domain.outbound.gateway import OutboundGateway
from app.workflows.daily import daily_deliverability
from app.workflows.pipeline import agent_context, run_campaign, send_due
from tests.security.test_real_adapter_switch import recording_graph
from tests.unit.test_dns_check import M365, dns

pytestmark = pytest.mark.db
AT = datetime(2026, 10, 13, 10, tzinfo=UTC)
DOMAIN = "outreach.northwind-demo.example"
GOOD = {(k[0].replace("outreach.example.co.uk", DOMAIN), k[1]): v for k, v in M365.items()}


def _login(c: TestClient, email: str, password: str, totp: str | None = None) -> TestClient:
    r = c.post(
        "/api/v1/auth/login", json={"email": email, "password": password} | ({"totp_code": totp} if totp else {})
    )
    assert r.status_code == 200, r.text
    c.headers["x-csrf-token"] = c.cookies["pos_csrf"]
    return c


def test_dns_check_is_stored_and_shown_and_never_trusted_blindly(clean_db, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.api import ops
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    with transaction(DbContext(role="operator"), clean_db) as conn:  # the demo seeds a domain marked OK; reset it
        conn.execute(
            text("UPDATE sending_domains SET spf_ok = false, dkim_ok = false, dmarc_ok = false, status = 'setup'")
        )
        conn.execute(text("UPDATE inboxes SET provider = 'msgraph'"))
        dom = conn.execute(text("SELECT id FROM sending_domains")).scalar()
    app = create_app()
    op = _login(
        TestClient(app),
        "op@pipelineos.example",
        demo.operator_password or "",
        pyotp.TOTP(demo.operator_totp_secret or "").now(),
    )

    monkeypatch.setattr(ops, "_dns_lookup", lambda: dns({}))
    r = op.post(f"/api/v1/sending-domains/{dom}/check").json()
    assert (r["spf_ok"], r["dkim_ok"], r["dmarc_ok"], r["status"]) == (False, False, False, "setup")
    assert len(r["issues"]) == 3 and r["providers"] == ["msgraph"]

    monkeypatch.setattr(ops, "_dns_lookup", lambda: dns(GOOD))
    r = op.post(f"/api/v1/sending-domains/{dom}/check").json()
    assert (r["spf_ok"], r["dkim_ok"], r["dmarc_ok"], r["status"], r["issues"]) == (True, True, True, "warming", [])
    assert op.get("/api/v1/sending-domains").json()[0]["checked_at"]

    client = _login(TestClient(app), demo.client_email, demo.client_password or "")
    assert client.post(f"/api/v1/sending-domains/{dom}/check").status_code == 403
    assert client.get("/api/v1/sending-domains").status_code == 403

    # the daily run re-checks: a record removed later is noticed
    monkeypatch.setattr(ops, "_dns_lookup", lambda: dns({}))
    totals = daily_deliverability(clean_db, now=AT, lookup=dns({**GOOD, (f"_dmarc.{DOMAIN}", "TXT"): []}))
    assert totals["domains_checked"] == 1 and totals["domains_ok"] == 0
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert conn.execute(text("SELECT dmarc_ok FROM sending_domains")).scalar() is False


def test_warmup_advances_only_on_days_with_real_sends(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    with transaction(DbContext(role="operator"), clean_db) as conn:
        conn.execute(text("UPDATE inboxes SET status = 'warming', daily_cap = 30, warmup_day = 0"))
    run_campaign(agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id), providers())
    _approve_all(clean_db, cipher, demo.tenant_id, demo.operator_id)
    send_due(clean_db, settings, cipher, demo.tenant_id, now=AT)  # dry-run day: warms nothing
    plan = warmup.WarmupPlan(start_cap=10, daily_increase=2, target_cap=12, days_to_active=1)
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        assert warmup.advance(conn, plan, day=AT.date(), healthy={}) == []
        assert conn.execute(text("SELECT daily_cap, warmup_day FROM inboxes")).one() == (10, 0)

    # all three keys on (test database only), one real send to the in-memory provider
    with transaction(DbContext(role="operator", user_id=demo.operator_id), clean_db) as conn:
        conn.execute(text("UPDATE tenants SET live_mode = true"))
        for item in conn.execute(text("SELECT id FROM golive_checklist_items")).scalars():
            conn.execute(text("SELECT golive_sign_off(:i, 'https://evidence.example')"), {"i": item})
        conn.execute(
            text("UPDATE messages SET scheduled_for = :at WHERE status = 'approved' AND sequence_step = 1"),
            {"at": AT + timedelta(days=1)},
        )
    sender, _calls = recording_graph()
    live = settings.model_copy(update={"email_adapter_mode": "live", "global_go_live": True})
    nxt = AT + timedelta(days=1)
    gw = OutboundGateway(live, clean_db, cipher, live_sender=sender, clock=lambda: nxt)
    out = send_due(clean_db, live, cipher, demo.tenant_id, now=nxt, gateway=gw)
    assert any(o.mode == "live" and o.status == "sent" for o in out)
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        inbox = conn.execute(text("SELECT id FROM inboxes")).scalar()
        # the gateway stamps sent_at with the database's real clock, so warm up the day the send really happened
        sent_day = conn.execute(text("SELECT max(sent_at) FROM messages WHERE status = 'sent'")).scalar().date()
        steps = warmup.advance(conn, plan, day=sent_day, healthy={inbox: True})
        assert [(s.warmup_day, s.daily_cap, s.promoted) for s in steps] == [(1, 12, True)]
        assert warmup.advance(conn, plan, day=sent_day, healthy={inbox: True}) == []  # once per day
        assert conn.execute(text("SELECT status, daily_cap FROM inboxes")).one() == ("active", 12)
        assert conn.execute(text("SELECT count(*) FROM audit_log WHERE action = 'inbox.warmup_complete'")).scalar() == 1


def test_an_unhealthy_inbox_is_never_promoted(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    seed_demo(clean_db, settings)
    plan = warmup.WarmupPlan(start_cap=10, daily_increase=2, target_cap=12, days_to_active=1)
    with transaction(DbContext(role="operator"), clean_db) as conn:
        conn.execute(text("UPDATE inboxes SET status = 'warming', warmup_day = 5"))
        assert warmup.advance(conn, plan, day=AT.date(), healthy={}) == []  # no live sends at all
        assert conn.execute(text("SELECT status FROM inboxes")).scalar() == "warming"


def test_readiness_lists_what_is_missing_and_signs_nothing(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    op = _login(
        TestClient(create_app()),
        "op@pipelineos.example",
        demo.operator_password or "",
        pyotp.TOTP(demo.operator_totp_secret or "").now(),
    )
    items = {i["key"]: i for i in op.get("/api/v1/governance/readiness").json()}
    assert set(items) == {
        "company_details",
        "uk_rules_reviewed",
        "uk_enabled",
        "tax_rules",
        "sending_domains",
        "inboxes_warmed",
        "providers",
        "pilot_client",
        "checklist",
        "switch",
    }
    assert items["company_details"]["state"] == "todo"  # placeholders in config/supplier.yaml
    assert items["uk_rules_reviewed"]["state"] == "todo"
    assert items["uk_enabled"]["detail"] == "Enabled without a recorded review (demo only)"
    assert items["checklist"]["state"] == "todo" and items["switch"]["detail"].startswith("Off")
    assert items["pilot_client"]["state"] == "done"
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert conn.execute(text("SELECT count(signed_off_at) FROM golive_checklist_items")).scalar() == 0
