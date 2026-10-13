"""The three-key switch with a REAL provider wired in (Microsoft Graph, over a recording in-process transport):
nothing reaches the provider until the adapter mode, the client's live flag AND global go-live (with the signed
checklist) are all on. Also: the Graph webhook rejects anything it can't authenticate."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from app.adapters.email.msgraph import GraphEmailSender
from app.adapters.inbound.msgraph import client_state
from app.adapters.msgraph_auth import GraphCredentials
from app.db.session import DbContext, transaction
from app.demo.conversations import _approve_all
from app.demo.seed import providers, seed_demo
from app.domain.outbound.gateway import OutboundGateway
from app.domain.outbound.providers import ProviderNotConfigured, calendar_gateway, email_senders
from app.workflows.pipeline import agent_context, run_campaign, send_due

pytestmark = pytest.mark.db
AT = datetime(2026, 10, 13, 10, tzinfo=UTC)


def recording_graph() -> tuple[GraphEmailSender, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(202)

    creds = GraphCredentials("tid", "cid", "secret", transport=httpx.MockTransport(handler))
    return GraphEmailSender(creds), calls


@pytest.fixture
def approved(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    run_campaign(agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id), providers())
    _approve_all(clean_db, cipher, demo.tenant_id, demo.operator_id)
    return demo


def test_real_provider_is_untouched_until_all_three_keys_are_on(approved, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    sender, calls = recording_graph()
    live_settings = settings.model_copy(update={"email_adapter_mode": "live"})  # key 1 only
    gw = OutboundGateway(live_settings, clean_db, cipher, live_sender=sender, clock=lambda: AT)
    out = send_due(clean_db, live_settings, cipher, approved.tenant_id, now=AT, gateway=gw)
    assert out and {o.mode for o in out} == {"dry_run"} and calls == []

    with transaction(DbContext(role="operator", user_id=approved.operator_id), clean_db) as conn:  # key 2
        conn.execute(text("UPDATE tenants SET live_mode = true WHERE id = :t"), {"t": approved.tenant_id})
        # bring the first follow-ups forward so they are due now
        conn.execute(
            text("UPDATE messages SET scheduled_for = :at WHERE status = 'approved' AND sequence_step = 1"),
            {"at": AT},
        )
    assert calls == []
    # key 3: global go-live with the checklist signed (test database only; the owner does this for real)
    with transaction(DbContext(role="operator", user_id=approved.operator_id), clean_db) as conn:
        for item in conn.execute(text("SELECT id FROM golive_checklist_items")).scalars():
            conn.execute(text("SELECT golive_sign_off(:i, 'https://evidence.example')"), {"i": item})
    all_on = settings.model_copy(update={"email_adapter_mode": "live", "global_go_live": True})
    gw = OutboundGateway(all_on, clean_db, cipher, live_sender=sender, clock=lambda: AT)
    out = send_due(clean_db, all_on, cipher, approved.tenant_id, now=AT, gateway=gw)
    live = [o for o in out if o.mode == "live"]
    assert live and all(o.status == "sent" for o in live)
    sends = [c for c in calls if c.url.path.endswith("/sendMail")]
    assert len(sends) == len(live)
    import base64

    assert b"List-Unsubscribe-Post: List-Unsubscribe=One-Click" in base64.b64decode(sends[0].content)


def test_provider_factory_respects_modes(settings, cipher) -> None:  # type: ignore[no-untyped-def]
    assert email_senders(settings) == (None, None)  # mock: nothing real is even built
    with pytest.raises(ProviderNotConfigured):
        email_senders(settings.model_copy(update={"email_adapter_mode": "live"}))
    live, sandbox = email_senders(settings.model_copy(update={"email_adapter_mode": "sandbox"}))
    assert live is None and sandbox is not None and sandbox.name == "email.sink"
    gw = calendar_gateway(settings, cipher, "a@x.example")
    assert gw.live_adapter is None and gw.adapter.name == "calendar.mock"


def test_graph_webhook_validation_and_authentication(clean_db, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.api.webhooks as hooks
    from app.api.deps import settings_dep
    from app.main import create_app

    demo = seed_demo(clean_db, settings)
    with transaction(DbContext(role="operator"), clean_db) as conn:
        conn.execute(text("UPDATE inboxes SET provider = 'msgraph' WHERE tenant_id = :t"), {"t": demo.tenant_id})
        mailbox = conn.execute(text("SELECT address FROM inboxes WHERE tenant_id = :t"), {"t": demo.tenant_id}).scalar()
    secret = "w" * 32
    app = create_app()
    app.dependency_overrides[settings_dep] = lambda: settings.model_copy(
        update={"msgraph_webhook_secret": SecretStr(secret)}
    )
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(hooks, "_enqueue", lambda mb, mid: queued.append((mb, mid)))
    c = TestClient(app)
    r = c.post("/webhooks/msgraph?validationToken=abc%20123")
    assert r.status_code == 200 and r.text == "abc 123" and r.headers["content-type"].startswith("text/plain")
    good = {
        "changeType": "created",
        "clientState": client_state(secret.encode(), mailbox),
        "resourceData": {"id": "AAMk1"},
    }
    forged = {**good, "clientState": "nope"}
    r = c.post("/webhooks/msgraph", json={"value": [good, forged, {**good, "changeType": "deleted"}]})
    assert r.status_code == 202 and queued == [(mailbox, "AAMk1")]
    with transaction(DbContext(role="operator"), clean_db) as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM audit_log WHERE action = 'webhook.msgraph.rejected'")).scalar() == 1
        )
    assert (
        c.post("/webhooks/msgraph", content=b"not json", headers={"content-type": "application/json"}).status_code
        == 400
    )
    app.dependency_overrides[settings_dep] = lambda: settings  # no secret configured -> endpoint disabled
    assert c.post("/webhooks/msgraph", json={"value": [good]}).status_code == 404
