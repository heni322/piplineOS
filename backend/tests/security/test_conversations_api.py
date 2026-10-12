"""Phase 4 API: escalations inbox, DP-request tracker with MFA-protected erasure, meetings (masked list, logged brief,
attendance), dev inbound injector, and the public single-use reschedule link."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.adapters.inbound.base import InboundEmail
from app.db.session import DbContext, transaction
from app.demo.conversations import FIRST_REPLIES, _approve_all, _reply
from app.demo.seed import providers, seed_demo
from app.domain.suppression import unsubscribe
from app.workflows.pipeline import agent_context, process_inbound, run_campaign, send_due
from tests.factories import make_tenant_user

pytestmark = pytest.mark.db
CONTOSO, BLUE, ADVENTURE = "Contoso Managed IT Ltd", "Blue Yonder Managed Services Ltd", "Adventure Works MSP Ltd"


def _start() -> datetime:
    """A simulated Tuesday 10:00 UTC in the future, so the booked meeting is still ahead of the real clock."""
    d = datetime.now(UTC).date() + timedelta(days=2)
    while d.weekday() != 1:
        d += timedelta(days=1)
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=UTC)


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    import app.api.v1.auth as auth_mod
    from app.main import create_app

    auth_mod._ip_buckets.clear()
    demo = seed_demo(clean_db, settings, operator_email="op@pipelineos.example")
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    run_campaign(ctx, providers())
    start = _start()
    _approve_all(clean_db, cipher, demo.tenant_id, demo.operator_id)
    send_due(clean_db, settings, cipher, demo.tenant_id, now=start)
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        emails = {
            r.name: cipher.decrypt(conn, demo.tenant_id, "prospects.email", r.email_enc)
            for r in conn.execute(
                text(
                    "SELECT c.name, p.email_enc FROM leads l JOIN companies c ON c.id = l.company_id "
                    "JOIN prospects p ON p.id = l.prospect_id WHERE l.state = 'contacted'"
                )
            )
        }

    def say(company: str, body: str, at: datetime, kind: str = "outreach") -> None:
        m = _reply(clean_db, demo.tenant_id, company, body, at, kind=kind)
        msg = InboundEmail(m.provider_message_id, emails[company], m.subject, body, at, m.in_reply_to)
        process_inbound(ctx, [msg], providers(), now=at)

    say(CONTOSO, FIRST_REPLIES[CONTOSO], start + timedelta(hours=1))
    _approve_all(clean_db, cipher, demo.tenant_id, demo.operator_id)
    send_due(clean_db, settings, cipher, demo.tenant_id, now=start + timedelta(hours=2))
    say(CONTOSO, "Option 2 works for me, thanks.", start + timedelta(hours=3), kind="agent_reply")
    say(BLUE, "Please delete all the data you hold about me.", start + timedelta(hours=1))
    say(ADVENTURE, "Where did you get my details?", start + timedelta(hours=1))

    c = TestClient(create_app())
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
    return c, demo, emails


def _dpr(c: TestClient, rtype: str) -> dict:  # type: ignore[type-arg]
    return next(d for d in c.get("/api/v1/data-protection-requests").json() if d["request_type"] == rtype)


def test_escalations_inbox_lists_without_pii_and_resolves(world) -> None:  # type: ignore[no-untyped-def]
    c, _, emails = world
    r = c.get("/api/v1/escalations")
    assert r.status_code == 200
    items = r.json()
    assert {i["reason"] for i in items} == {"data_protection"}
    assert not any(e in r.text for e in emails.values())
    e = items[0]
    assert c.post(f"/api/v1/escalations/{e['id']}/resolve", json={"resolution": "ok"}).status_code == 422
    assert c.post(f"/api/v1/escalations/{e['id']}/resolve", json={"resolution": "handled by DPO"}).status_code == 200
    assert c.post(f"/api/v1/escalations/{e['id']}/resolve", json={"resolution": "again pls"}).status_code == 409
    assert len(c.get("/api/v1/escalations").json()) == len(items) - 1


def test_dpr_tracker_shows_deadline_and_regime(world) -> None:  # type: ignore[no-untyped-def]
    c, _, _ = world
    rows = c.get("/api/v1/data-protection-requests").json()
    assert {d["request_type"] for d in rows} == {"erasure", "access"}
    for d in rows:
        assert d["regime"] == "uk_gdpr" and d["days_remaining"] >= 27 and not d["overdue"]
        assert d["response_draft"]  # drafted for a human to send
    erasure = _dpr(c, "erasure")
    r = c.post(
        f"/api/v1/data-protection-requests/{erasure['id']}/status", json={"status": "completed", "note": "x" * 5}
    )
    assert r.status_code == 409  # erasure only via the MFA-protected endpoint


def test_erasure_requires_mfa_and_identity_attestation(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    c, demo, _ = world
    erasure, access = _dpr(c, "erasure"), _dpr(c, "access")
    url = f"/api/v1/data-protection-requests/{erasure['id']}/complete-erasure"
    assert c.post(url, json={"totp_code": "000000", "identity_verified": True}).status_code == 403
    assert c.post(url, json={"totp_code": "123456", "identity_verified": False}).status_code == 422
    code = pyotp.TOTP(demo.operator_totp_secret).now()
    other = f"/api/v1/data-protection-requests/{access['id']}/complete-erasure"
    assert c.post(other, json={"totp_code": code, "identity_verified": True}).status_code == 409
    r = c.post(url, json={"totp_code": code, "identity_verified": True})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert _dpr(c, "erasure")["status"] == "completed"
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM leads WHERE state = 'erased'")).scalar() == 1


def test_meetings_are_masked_and_brief_access_is_logged(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    c, _, emails = world
    r = c.get("/api/v1/meetings")
    assert r.status_code == 200
    (m,) = r.json()
    assert m["company"] == CONTOSO and m["prospect"] == "A*** M***" and m["status"] == "booked"
    assert "Alex" not in r.text and emails[CONTOSO] not in r.text
    b = c.get(f"/api/v1/meetings/{m['id']}/brief")
    assert b.status_code == 200 and "Alex" in b.json()["brief"]
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert conn.execute(text("SELECT reason FROM pii_access_log")).scalars().all() == ["pre-meeting brief"]
    # attendance cannot be recorded before the meeting has happened
    assert c.post(f"/api/v1/meetings/{m['id']}/attendance", json={"status": "attended"}).status_code == 409


def test_tenant_users_cannot_resolve_escalations_or_erase(world, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    c, demo, _ = world
    make_tenant_user(clean_db, settings, cipher, demo.tenant_id, email="admin@northwind.example")
    t = TestClient(c.app)
    r = t.post("/api/v1/auth/login", json={"email": "admin@northwind.example", "password": "tenant-password-123"})
    assert r.status_code == 200
    t.headers["x-csrf-token"] = t.cookies["pos_csrf"]
    erasure = _dpr(c, "erasure")
    assert t.get("/api/v1/escalations").status_code == 403
    assert (
        t.post(
            f"/api/v1/data-protection-requests/{erasure['id']}/complete-erasure",
            json={"totp_code": "123456", "identity_verified": True},
        ).status_code
        == 403
    )
    assert (
        t.post(
            "/api/v1/dev/inbound", json={"tenant_id": str(demo.tenant_id), "from_email": "a@b.example", "body": "hi"}
        ).status_code
        == 403
    )
    assert t.get("/api/v1/data-protection-requests").status_code == 200  # tenant sees its own requests
    assert t.get("/api/v1/meetings").status_code == 200


def test_unauthenticated_requests_are_refused(world) -> None:  # type: ignore[no-untyped-def]
    c, _, _ = world
    anon = TestClient(c.app)
    for path in ("/api/v1/escalations", "/api/v1/data-protection-requests", "/api/v1/meetings"):
        assert anon.get(path).status_code == 401


def test_dev_inbound_injector_runs_agent4(world) -> None:  # type: ignore[no-untyped-def]
    c, demo, _ = world
    r = c.post(
        "/api/v1/dev/inbound",
        json={"tenant_id": str(demo.tenant_id), "from_email": "nobody@else.example", "body": "unsubscribe"},
    )
    assert r.status_code == 200 and r.json()["classification"] == "unmatched"


# ------------------------------------------------------------------------------------------- public reschedule
def _token(clean_db, settings, demo) -> str:  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        tid = conn.execute(text("SELECT id FROM reschedule_tokens WHERE used_at IS NULL")).scalar_one()
    return unsubscribe.sign(settings.key("unsubscribe_signing_key"), tid, unsubscribe.RESCHEDULE)


def test_reschedule_link_lists_slots_and_moves_the_call_once(world, clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    c, demo, _ = world
    anon = TestClient(c.app)
    token = _token(clean_db, settings, demo)
    page = anon.get(f"/r/{token}")
    assert page.status_code == 200 and "Choose a new time" in page.text
    values = re.findall(r'name="slot" value="([^"]+)"', page.text)
    assert len(values) == 3
    assert anon.post(f"/r/{token}", data={"slot": "2030-01-01T10:00:00+00:00"}).status_code == 409  # not offered
    done = anon.post(f"/r/{token}", data={"slot": values[0]})
    assert done.status_code == 200 and "Your call has moved" in done.text
    assert anon.post(f"/r/{token}", data={"slot": values[1]}).status_code == 400  # single use
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        m = conn.execute(text("SELECT starts_at, reschedule_count FROM meetings")).one()
        assert m.starts_at == datetime.fromisoformat(values[0]) and m.reschedule_count == 1
        assert conn.execute(text("SELECT count(*) FROM reschedule_tokens WHERE used_at IS NULL")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM dry_run_log WHERE effect_type = 'calendar_cancel'")).scalar()


def test_reschedule_link_rejects_forged_and_cross_purpose_tokens(world, clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    c, demo, _ = world
    anon = TestClient(c.app)
    token = _token(clean_db, settings, demo)
    assert anon.get("/r/not-a-token").status_code == 400
    assert anon.get(f"/r/{token[:-2]}AA").status_code == 400
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        tid = conn.execute(text("SELECT id FROM reschedule_tokens")).scalar_one()
    unsub_style = unsubscribe.sign(settings.key("unsubscribe_signing_key"), tid)  # right id, wrong purpose
    assert anon.get(f"/r/{unsub_style}").status_code == 400
    assert anon.post(f"/u/{token}").status_code == 400  # a reschedule token is not an unsubscribe token
    big = anon.post(
        f"/r/{token}", content=b"slot=" + b"x" * 500, headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert big.status_code == 400


def test_agent_replies_reach_the_approval_desk_with_context(world) -> None:  # type: ignore[no-untyped-def]
    c, demo, emails = world
    r = c.post(
        "/api/v1/dev/inbound",
        json={
            "tenant_id": str(demo.tenant_id),
            "from_email": emails["Woodgrove IT plc"],
            "body": "Thanks Jordan, not right now. Maybe after Christmas.",
        },
    )
    assert r.status_code == 200 and "reply_drafted_for_approval" in r.json()["actions"]
    items = c.get("/api/v1/approvals").json()["items"]
    reply = next(i for i in items if i["kind"] == "agent_reply")
    assert reply["company"] == "Woodgrove IT plc" and len(reply["sequence"]) == 1
    assert "after Christmas" in reply["prospect_reply"]
    assert all(i["kind"] == "outreach" for i in items if i is not reply)
