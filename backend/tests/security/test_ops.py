"""Readiness, metrics (token-protected, aggregate only), kill switch end to end, bounces, inbox resume."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from app.adapters.inbound.base import InboundEmail
from app.db.session import DbContext, transaction
from app.demo.conversations import _approve_all
from app.demo.seed import providers, seed_demo
from app.domain.deliverability.killswitch import Thresholds, evaluate
from app.workflows.pipeline import agent_context, process_inbound, run_campaign, send_due

pytestmark = pytest.mark.db
AT = datetime(2026, 10, 13, 10, tzinfo=UTC)


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.api.deps import settings_dep
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    run_campaign(ctx, providers())
    _approve_all(clean_db, cipher, demo.tenant_id, demo.operator_id)
    send_due(clean_db, settings, cipher, demo.tenant_id, now=AT)
    app = create_app()
    app.dependency_overrides[settings_dep] = lambda: settings.model_copy(update={"metrics_token": SecretStr("m" * 32)})
    c = TestClient(app)
    r = c.post(
        "/api/v1/auth/login",
        json={
            "email": "op@pipelineos.example",
            "password": demo.operator_password,
            "totp_code": pyotp.TOTP(demo.operator_totp_secret or "").now(),
        },
    )
    assert r.status_code == 200
    c.headers["x-csrf-token"] = c.cookies["pos_csrf"]
    return c, demo, ctx


def test_readiness_reports_each_dependency(world) -> None:  # type: ignore[no-untyped-def]
    c, _, _ = world
    r = TestClient(c.app).get("/api/v1/ready")
    body = r.json()
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from tests.conftest import BACKEND

    head = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_current_head()
    assert body["database"] == "ok" and body["schema"] == head
    assert r.status_code == (200 if body["valkey"] == "ok" else 503) and body["ready"] == (r.status_code == 200)


def test_metrics_need_the_token_and_hold_no_personal_data(world) -> None:  # type: ignore[no-untyped-def]
    c, _, _ = world
    anon = TestClient(c.app)
    assert anon.get("/metrics").status_code == 404
    assert anon.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 404
    r = anon.get("/metrics", headers={"Authorization": "Bearer " + "m" * 32})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    for name in (
        "pipelineos_global_go_live 0.0",
        "pipelineos_pending_approvals",
        "pipelineos_dpr_overdue",
        "pipelineos_inboxes_paused 0.0",
        'pipelineos_clients_paused{by="client"} 0.0',
        'pipelineos_clients_paused{by="operator"} 0.0',
        "pipelineos_campaigns_pilot_full",
        'pipelineos_sends_last_hour{mode="dry_run"}',
        "pipelineos_vat_threshold_pct",
    ):
        assert name in r.text, name
    assert "@" not in r.text  # aggregate counts only


@contextlib.contextmanager
def superuser():  # type: ignore[no-untyped-def]
    """Test-database superuser connection, for fabricating history the app itself could never write."""
    import psycopg

    from tests.conftest import ADMIN_URL, TEST_DB

    with psycopg.connect(ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}", autocommit=True) as conn:
        yield conn


def _count(engine, sql: str, **p) -> int:  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator"), engine) as conn:
        return int(conn.execute(text(sql), p).scalar() or 0)


HISTORY = """INSERT INTO messages (tenant_id, lead_id, campaign_id, inbox_id, recipient_hash, recipient_domain_hash,
  status, idempotency_key, sent_at, mode_at_send, last_error, kind)
  VALUES (%s, %s, %s, %s, '\\x00', '\\x00', %s, %s, now() - interval '1 day', 'live', %s, 'agent_reply')"""


def test_kill_switch_pauses_a_bouncing_inbox_and_the_gateway_stops(world, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    c, demo, _ = world
    with superuser() as conn:  # synthetic live history: 60 sent, 3 hard bounces (5%); bypasses the send gate
        inbox, lead, camp = conn.execute(
            "SELECT i.id, m.lead_id, m.campaign_id FROM inboxes i JOIN messages m ON m.tenant_id = i.tenant_id "
            "WHERE i.tenant_id = %s LIMIT 1",
            (demo.tenant_id,),
        ).fetchone()
        conn.execute("SET session_replication_role = replica")
        for i in range(60):
            conn.execute(
                HISTORY,
                (
                    demo.tenant_id,
                    lead,
                    camp,
                    inbox,
                    "bounced" if i < 3 else "sent",
                    f"hist-{i}",
                    "hard_bounce" if i < 3 else None,
                ),
            )
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        res = evaluate(conn, demo.tenant_id, datetime.now(UTC), Thresholds())
    assert res[0]["paused"] and res[0]["reasons"] == ["HARD_BOUNCE_RATE"] and res[0]["sent"] == 60
    with transaction(DbContext(role="operator"), clean_db) as conn:  # follow-ups due now...
        conn.execute(
            text("UPDATE messages SET scheduled_for = :a WHERE status = 'approved' AND sequence_step = 1"),
            {"a": AT + timedelta(days=1)},
        )
    out = send_due(clean_db, settings, cipher, demo.tenant_id, now=AT + timedelta(days=1, hours=1))
    assert out and all("INBOX_UNAVAILABLE" in o.reason_codes for o in out)  # ...are held back, not sent
    metrics_text = TestClient(c.app).get("/metrics", headers={"Authorization": "Bearer " + "m" * 32}).text
    assert "pipelineos_inboxes_paused 1.0" in metrics_text
    box = c.get("/api/v1/inboxes").json()[0]
    assert box["status"] == "paused"
    assert c.post(f"/api/v1/inboxes/{box['id']}/resume", json={"reason": "short"}).status_code == 422
    assert (
        c.post(
            f"/api/v1/inboxes/{box['id']}/resume",
            json={"reason": "Removed the bad list segment and re-verified addresses"},
        ).status_code
        == 204
    )
    assert c.get("/api/v1/inboxes").json()[0]["status"] == "active"


def test_hard_bounce_suppresses_and_never_reaches_agent4(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    _, demo, ctx = world
    with superuser() as conn:  # make one dry-run message look live-sent, so a real bounce could refer to it
        mid, pid = conn.execute(
            "SELECT id, provider_message_id FROM messages WHERE tenant_id = %s AND status = 'dry_run_sent' LIMIT 1",
            (demo.tenant_id,),
        ).fetchone()
        conn.execute("SET session_replication_role = replica")
        pid = "<0123456789abcdef0123456789abcdef@outreach.northwind-demo.example>"  # a real Message-ID shape
        conn.execute(
            "UPDATE messages SET status = 'sent', mode_at_send = 'live', provider_message_id = %s WHERE id = %s",
            (pid, mid),
        )
    calls_before = _count(clean_db, "SELECT count(*) FROM llm_calls")
    ndr = InboundEmail(
        "<ndr-1@mx.example>",
        "MAILER-DAEMON@mx.contoso.example",
        "Undeliverable: Service desk",
        f"Remote server returned '550 5.1.1 User unknown'.\nOriginal message: {pid}",
        AT,
        None,
    )
    out = process_inbound(ctx, [ndr], providers(), now=AT)[0]
    assert out.classification == "bounce" and out.actions == ["bounce:hard", "suppressed"]
    assert _count(clean_db, "SELECT count(*) FROM llm_calls") == calls_before  # no AI call for a bounce
    assert _count(clean_db, "SELECT count(*) FROM suppression_list WHERE reason = 'bounce_hard'") == 1
    assert (
        _count(
            clean_db,
            "SELECT count(*) FROM leads l JOIN messages m ON m.lead_id = l.id "
            "WHERE m.id = :m AND l.state = 'suppressed'",
            m=mid,
        )
        == 1
    )
    assert _count(clean_db, "SELECT count(*) FROM messages WHERE id = :m AND status = 'bounced'", m=mid) == 1
    soft = InboundEmail(
        "<ndr-2@mx.example>",
        "postmaster@mx.example",
        "Delivery Status Notification (Delay)",
        "4.4.7 Message delayed",
        AT,
        None,
    )
    assert process_inbound(ctx, [soft], providers(), now=AT)[0].actions == ["bounce:unmatched"]


def test_stops_and_pilot_limits_show_up_as_alertable_metrics(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    c, demo, _ = world
    token = {"Authorization": "Bearer " + "m" * 32}
    campaign = c.get("/api/v1/campaigns").json()[0]
    assert campaign["pilot_contacts_used"] > 0
    assert c.put(f"/api/v1/campaigns/{campaign['id']}/pilot-limit", json={"max_contacts": 1}).status_code == 200
    with transaction(DbContext(tenant_id=demo.tenant_id, role="operator"), clean_db) as conn:
        conn.execute(
            text(
                "UPDATE tenants SET status = 'paused', paused_reason = 'Client: complaint', paused_at = now() "
                "WHERE id = :t"
            ),
            {"t": demo.tenant_id},
        )
    m = TestClient(c.app).get("/metrics", headers=token).text
    assert 'pipelineos_clients_paused{by="client"} 1.0' in m
    assert 'pipelineos_clients_paused{by="operator"} 0.0' in m
    assert "pipelineos_campaigns_pilot_full 1.0" in m
