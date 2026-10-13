"""Google Workspace behind the same gates as Microsoft 365: nothing reaches Gmail until all three live keys are on;
the live sender routes by the inbox's provider; the Gmail push webhook verifies Google's signed token before
queueing anything; the reply cursor only moves forward and replies reach Agent 4."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from app.adapters.email.base import EmailPayload
from app.adapters.email.gmail import GmailSender
from app.adapters.google_auth import GoogleCredentials
from app.adapters.inbound.base import InboundEmail
from app.db.session import DbContext, transaction
from app.demo.conversations import _approve_all
from app.demo.seed import providers, seed_demo
from app.domain.outbound.gateway import OutboundGateway
from app.domain.outbound.providers import ProviderNotConfigured, RoutingEmailSender, email_senders
from app.workflows.pipeline import agent_context, run_campaign, send_due
from tests.contract.test_google_adapters import AUD, PUSH_SA, SA, oidc, verifier

pytestmark = pytest.mark.db
AT = datetime(2026, 10, 13, 10, tzinfo=UTC)


def recording_gmail() -> tuple[GmailSender, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path == "/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if req.url.path.endswith("/messages/send"):
            return httpx.Response(200, json={"id": f"g{len(calls)}"})
        return httpx.Response(
            200, json={"payload": {"headers": [{"name": "Message-ID", "value": f"<g{len(calls)}@mail.gmail.com>"}]}}
        )

    return GmailSender(GoogleCredentials(SA, transport=httpx.MockTransport(handler))), calls


@pytest.fixture
def approved(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    with transaction(DbContext(role="operator", user_id=demo.operator_id), clean_db) as conn:
        conn.execute(text("UPDATE inboxes SET provider = 'google' WHERE tenant_id = :t"), {"t": demo.tenant_id})
    run_campaign(agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id), providers())
    _approve_all(clean_db, cipher, demo.tenant_id, demo.operator_id)
    return demo


def test_gmail_is_untouched_until_all_three_keys_are_on(approved, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    gmail, calls = recording_gmail()
    router = RoutingEmailSender({"google": gmail})
    key1 = settings.model_copy(update={"email_adapter_mode": "live"})
    out = send_due(
        clean_db,
        key1,
        cipher,
        approved.tenant_id,
        now=AT,
        gateway=OutboundGateway(key1, clean_db, cipher, live_sender=router, clock=lambda: AT),
    )
    assert out and {o.mode for o in out} == {"dry_run"} and calls == []

    with transaction(DbContext(role="operator", user_id=approved.operator_id), clean_db) as conn:
        conn.execute(text("UPDATE tenants SET live_mode = true WHERE id = :t"), {"t": approved.tenant_id})
        conn.execute(
            text("UPDATE messages SET scheduled_for = :at WHERE status = 'approved' AND sequence_step = 1"), {"at": AT}
        )
        for item in conn.execute(text("SELECT id FROM golive_checklist_items")).scalars():
            conn.execute(text("SELECT golive_sign_off(:i, 'https://evidence.example')"), {"i": item})
    all_on = settings.model_copy(update={"email_adapter_mode": "live", "global_go_live": True})
    out = send_due(
        clean_db,
        all_on,
        cipher,
        approved.tenant_id,
        now=AT,
        gateway=OutboundGateway(all_on, clean_db, cipher, live_sender=router, clock=lambda: AT),
    )
    live = [o for o in out if o.mode == "live"]
    assert live and all(o.status == "sent" for o in live)
    sends = [c for c in calls if c.url.path.endswith("/messages/send")]
    assert len(sends) == len(live)
    raw = base64.urlsafe_b64decode(json.loads(sends[0].content)["raw"] + "==")
    assert b"List-Unsubscribe-Post: List-Unsubscribe=One-Click" in raw
    with transaction(DbContext.system(approved.tenant_id), clean_db) as conn:
        ids = list(conn.execute(text("SELECT provider_message_id FROM messages WHERE mode_at_send = 'live'")).scalars())
    assert ids and all(i.endswith("@mail.gmail.com>") for i in ids)  # Gmail's own Message-ID, for reply matching


def test_routing_sends_only_through_the_inboxes_own_provider(settings) -> None:  # type: ignore[no-untyped-def]
    gmail, calls = recording_gmail()
    router = RoutingEmailSender({"google": gmail})
    p = EmailPayload("a@b.example", "x@outreach.example", "X", "s", "b", "k", provider="msgraph")
    with pytest.raises(ProviderNotConfigured):
        router.send(p)
    assert calls == []
    empty = settings.model_copy(update={"email_adapter_mode": "live", "google_service_account_json": SecretStr("")})
    with pytest.raises(ProviderNotConfigured):  # an empty GOOGLE_SERVICE_ACCOUNT_JSON= line is "not configured"
        email_senders(empty)
    only_google = settings.model_copy(
        update={"email_adapter_mode": "live", "google_service_account_json": SecretStr(SA)}
    )
    live, sandbox = email_senders(only_google)
    assert isinstance(live, RoutingEmailSender) and set(live.senders) == {"google"} and sandbox is None


@pytest.fixture
def webhook(approved, settings, monkeypatch):  # type: ignore[no-untyped-def]
    import app.api.webhooks as hooks
    from app.api.deps import settings_dep
    from app.main import create_app

    queued: list[tuple[str, int]] = []
    monkeypatch.setattr(hooks, "_enqueue_gmail", lambda mb, h: queued.append((mb, h)))
    monkeypatch.setattr(hooks, "_verifiers", {(AUD, PUSH_SA): verifier()})
    app = create_app()
    app.dependency_overrides[settings_dep] = lambda: settings.model_copy(
        update={"google_push_audience": AUD, "google_push_service_account": PUSH_SA}
    )
    return TestClient(app), queued, app


def _push(mailbox: str, history_id: int) -> dict[str, object]:
    data = base64.b64encode(json.dumps({"emailAddress": mailbox, "historyId": history_id}).encode()).decode()
    return {"message": {"data": data, "messageId": "1"}, "subscription": "projects/p/subscriptions/s"}


def test_gmail_webhook_verifies_googles_token_before_queueing(webhook, approved, clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    c, queued, app = webhook
    with transaction(DbContext(role="operator"), clean_db) as conn:
        mailbox = conn.execute(text("SELECT address FROM inboxes WHERE provider = 'google'")).scalar_one()
    assert c.post("/webhooks/google", json=_push(mailbox, 10)).status_code == 403  # no token
    forged = oidc(aud="https://evil.example/hook")
    assert c.post("/webhooks/google", json=_push(mailbox, 10), headers={"Authorization": forged}).status_code == 403
    assert queued == []
    ok = {"Authorization": oidc()}
    assert c.post("/webhooks/google", json=_push("stranger@elsewhere.example", 10), headers=ok).status_code == 204
    assert queued == []
    assert c.post("/webhooks/google", json=_push(mailbox, 77), headers=ok).status_code == 204
    assert queued == [(mailbox, 77)]
    with transaction(DbContext(role="operator"), clean_db) as conn:
        rejected = conn.execute(text("SELECT count(*) FROM audit_log WHERE action = 'webhook.gmail.rejected'")).scalar()
    assert rejected == 3
    from app.api.deps import settings_dep

    app.dependency_overrides[settings_dep] = lambda: settings  # not configured: the endpoint doesn't exist
    assert c.post("/webhooks/google", json=_push(mailbox, 1), headers=ok).status_code == 404


def test_gmail_history_cursor_moves_forward_and_replies_reach_agent_4(
    approved, clean_db, settings, cipher, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    import app.adapters.inbound.gmail as gmail_mod
    import app.domain.outbound.providers as prov
    from app.workflows.celery_app import gmail_history_task

    send_due(clean_db, settings, cipher, approved.tenant_id, now=AT)  # dry-run sends, so there is something to reply to
    with transaction(DbContext.system(approved.tenant_id), clean_db) as conn:
        mailbox = conn.execute(text("SELECT address FROM inboxes WHERE provider = 'google'")).scalar_one()
        sent_id, lead_id = conn.execute(
            text("SELECT provider_message_id, lead_id FROM messages WHERE status = 'dry_run_sent' LIMIT 1")
        ).one()
    asked: list[int] = []

    class FakeInbox:
        def __init__(self, creds: object, mb: str) -> None:
            assert mb == mailbox

        def fetch_since(self, start: int) -> tuple[list[InboundEmail], int]:
            asked.append(start)
            reply = InboundEmail(
                "<r1@contoso.example>",
                "someone@contoso.example",
                "Re: hello",
                "Please take me off your list.",
                AT,
                in_reply_to=sent_id,
            )
            return [reply], 150

    monkeypatch.setattr(gmail_mod, "GmailInbox", FakeInbox)
    monkeypatch.setattr(prov, "google_credentials", lambda s: object())

    assert gmail_history_task(mailbox, 100) == 0  # first push after the watch: just remember where we are
    assert asked == []
    assert gmail_history_task(mailbox, 140) == 1
    assert asked == [100]
    with transaction(DbContext.system(approved.tenant_id), clean_db) as conn:
        assert conn.execute(text("SELECT history_id FROM inboxes WHERE address = :a"), {"a": mailbox}).scalar() == 150
        assert conn.execute(text("SELECT count(*) FROM replies WHERE lead_id = :l"), {"l": lead_id}).scalar() == 1
    gmail_history_task(mailbox, 120)  # an older, late notification never moves the cursor back
    with transaction(DbContext.system(approved.tenant_id), clean_db) as conn:
        assert conn.execute(text("SELECT history_id FROM inboxes WHERE address = :a"), {"a": mailbox}).scalar() == 150
        assert conn.execute(text("SELECT count(*) FROM replies WHERE lead_id = :l"), {"l": lead_id}).scalar() == 1
