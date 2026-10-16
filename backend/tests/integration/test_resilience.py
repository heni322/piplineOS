"""Fallback mechanisms against the real database (ADR-0019): bounded send retries, dead-letter, bulkheads, model
outage deferral, bounce classes. An outage must defer work, never decide it."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.adapters.base import RateLimited, RetryableProviderError
from app.adapters.email.mock import MockEmailSender
from app.adapters.http import ProviderError
from app.adapters.inbound.base import InboundEmail
from app.db.session import DbContext, transaction
from app.domain.deliverability.killswitch import handle_bounce
from app.domain.outbound.gateway import MAX_SEND_ATTEMPTS, OutboundGateway
from app.workflows import outbox
from app.workflows.pipeline import send_due
from tests.factories import enable, make_lead, make_operator, make_tenant
from tests.integration.test_gateway import IN_HOURS, approved_message
from tests.integration.test_pilot_controls import _tenant as configured_tenant

pytestmark = pytest.mark.db


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    op = make_operator(clean_db, settings, cipher)
    enable(clean_db, op, "GB")
    return op


def _sandbox(settings, engine, cipher, sender, at=IN_HOURS):  # type: ignore[no-untyped-def]
    s2 = settings.model_copy(update={"email_adapter_mode": "sandbox"})
    return OutboundGateway(s2, engine, cipher, sandbox_sender=sender, clock=lambda: at)


def _msg(engine, tenant_id, mid) -> Any:  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(tenant_id), engine) as conn:
        return conn.execute(
            text("SELECT status, send_attempts, next_attempt_at, last_error FROM messages WHERE id = :m"), {"m": mid}
        ).one()


def _escalations(engine, tenant_id) -> list[str]:  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(tenant_id), engine) as conn:
        return list(conn.execute(text("SELECT reason FROM escalations ORDER BY created_at")).scalars())


# ------------------------------------------------------------------------------------------- send retries
def test_a_refused_send_backs_off_and_after_the_attempt_limit_a_human_is_told(
    clean_db, world, settings, cipher
) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    sender = MockEmailSender(fail_with=RetryableProviderError("503"))
    g = _sandbox(settings, clean_db, cipher, sender)
    out = g.send_message(lead.tenant_id, mid)
    m = _msg(clean_db, lead.tenant_id, mid)
    assert out.status == "retry_later" and m.status == "failed_retryable" and m.send_attempts == 1
    assert m.next_attempt_at == IN_HOURS + timedelta(minutes=2)
    for _ in range(MAX_SEND_ATTEMPTS - 1):
        out = g.send_message(lead.tenant_id, mid)
    m = _msg(clean_db, lead.tenant_id, mid)
    assert out.status == "failed" and m.status == "failed_permanent" and m.send_attempts == MAX_SEND_ATTEMPTS
    assert _escalations(clean_db, lead.tenant_id) == ["send_failed"]
    assert sender.sent == []
    assert g.send_message(lead.tenant_id, mid).status == "skipped"  # never retried after giving up


def test_rate_limit_retry_after_is_honoured(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    _sandbox(settings, clean_db, cipher, MockEmailSender(fail_with=RateLimited("1200"))).send_message(
        lead.tenant_id, mid
    )
    assert _msg(clean_db, lead.tenant_id, mid).next_attempt_at == IN_HOURS + timedelta(minutes=20)


def test_a_definite_refusal_fails_at_once_without_retries(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    out = _sandbox(settings, clean_db, cipher, MockEmailSender(fail_with=ProviderError(400, "bad"))).send_message(
        lead.tenant_id, mid
    )
    m = _msg(clean_db, lead.tenant_id, mid)
    assert out.status == "failed" and m.status == "failed_permanent" and m.last_error == "ProviderError 400"
    assert _escalations(clean_db, lead.tenant_id) == ["send_failed"]


class _Router(MockEmailSender):
    """Fails one message with a given error; sends the rest."""

    def __init__(self, fail: dict[str, Exception]) -> None:
        super().__init__()
        self.fail = fail

    def send(self, payload):  # type: ignore[no-untyped-def, override]
        for key, exc in self.fail.items():
            if key in payload.idempotency_key:
                raise exc
        return super().send(payload)


def test_send_loop_backs_off_isolates_unknown_outcomes_and_pauses_a_refusing_inbox(
    clean_db, world, settings, cipher
) -> None:  # type: ignore[no-untyped-def]
    tenant = configured_tenant(clean_db)
    leads = [make_lead(clean_db, settings, cipher, tenant) for _ in range(3)]
    mids = [approved_message(clean_db, settings, cipher, lead) for lead in leads]
    with transaction(DbContext.system(tenant), clean_db) as conn:
        keys = [
            conn.execute(text("SELECT idempotency_key FROM messages WHERE id = :m"), {"m": m}).scalar_one()
            for m in mids
        ]
        # three different inboxes, so the per-inbox pause can be seen in isolation
        inbox_ids = [conn.execute(text("SELECT inbox_id FROM messages WHERE id = :m"), {"m": m}).scalar() for m in mids]
    assert len(set(inbox_ids)) == 3
    sender = _Router({keys[0]: ConnectionResetError("outcome unknown"), keys[1]: RetryableProviderError("503")})
    s2 = settings.model_copy(update={"email_adapter_mode": "sandbox"})
    g = OutboundGateway(s2, clean_db, cipher, sandbox_sender=sender, clock=lambda: IN_HOURS)
    outcomes = send_due(clean_db, s2, cipher, tenant, now=IN_HOURS, gateway=g)
    # message 0 crashed (left for reconciliation) but did not stop message 2 from being sent
    assert _msg(clean_db, tenant, mids[0]).status == "sending"
    assert _msg(clean_db, tenant, mids[1]).status == "failed_retryable"
    assert _msg(clean_db, tenant, mids[2]).status == "sent"
    assert sorted(o.status for o in outcomes) == ["retry_later", "sent"]
    # one minute later the refused message is still backing off: it is not picked up again
    later = send_due(clean_db, s2, cipher, tenant, now=IN_HOURS + timedelta(minutes=1), gateway=g)
    assert later == []


def test_one_refusal_holds_the_rest_of_that_inbox_for_this_tick(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    tenant = configured_tenant(clean_db)
    lead_a = make_lead(clean_db, settings, cipher, tenant)
    lead_b = make_lead(clean_db, settings, cipher, tenant)
    m_a = approved_message(clean_db, settings, cipher, lead_a)
    m_b = approved_message(clean_db, settings, cipher, lead_b)
    with transaction(DbContext.system(tenant), clean_db) as conn:  # both from the same inbox
        conn.execute(text("UPDATE messages SET inbox_id = :i WHERE id = :m"), {"i": lead_a.inbox_id, "m": m_b})
    sender = MockEmailSender(fail_with=RateLimited("60"))
    s2 = settings.model_copy(update={"email_adapter_mode": "sandbox"})
    g = OutboundGateway(s2, clean_db, cipher, sandbox_sender=sender, clock=lambda: IN_HOURS)
    outcomes = send_due(clean_db, s2, cipher, tenant, now=IN_HOURS, gateway=g)
    assert [o.status for o in outcomes] == ["retry_later"]  # the second message was not even tried
    statuses = {_msg(clean_db, tenant, m_a).status, _msg(clean_db, tenant, m_b).status}
    assert statuses == {"failed_retryable", "approved"}


# ------------------------------------------------------------------------------------------- outbox
def test_a_poison_outbox_event_backs_off_is_dead_lettered_and_never_blocks_the_others(clean_db) -> None:  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(), clean_db) as conn:
        conn.execute(text("INSERT INTO outbox (topic, payload) VALUES ('poison', '{}'), ('ok', '{}')"))
    seen: list[str] = []

    def dispatch(topic: str, payload: dict[str, Any]) -> None:
        seen.append(topic)
        if topic == "poison":
            raise RuntimeError("cannot be handled")

    assert outbox.relay_batch(clean_db, dispatch) == 1  # the healthy event went through
    assert outbox.relay_batch(clean_db, dispatch) == 0  # the poison event is backing off: not retried at once
    assert seen == ["poison", "ok"]
    with transaction(DbContext.system(), clean_db) as conn:
        conn.execute(text("UPDATE outbox SET attempts = :n, next_attempt_at = NULL"), {"n": outbox.MAX_ATTEMPTS})
    assert outbox.relay_batch(clean_db, dispatch) == 0
    assert seen == ["poison", "ok"]  # dead-lettered: left for a human, never retried


# ------------------------------------------------------------------------------------------- bounces
def _bounce(lead, mid, engine, settings, cipher, body: str) -> InboundEmail:  # type: ignore[no-untyped-def]
    out = _sandbox(settings, engine, cipher, MockEmailSender()).send_message(lead.tenant_id, mid)
    assert out.status == "sent"
    return InboundEmail(
        provider_message_id=f"<b-{uuid.uuid4()}@mx.example>",
        from_email="mailer-daemon@mx.contoso.example",
        subject="Undeliverable: Hello",
        body_text=body,
        received_at=IN_HOURS,
        in_reply_to=out.provider_message_id,
    )


def test_a_sender_side_policy_block_never_suppresses_the_person(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    msg = _bounce(lead, mid, clean_db, settings, cipher, "550 5.7.1 Message rejected: sender IP listed by Spamhaus")
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        out = handle_bounce(conn, settings.key("suppression_hash_key"), cipher, lead.tenant_id, msg)
        assert out == ["bounce:policy"]
        assert conn.execute(text("SELECT status FROM messages WHERE id = :m"), {"m": mid}).scalar() == "bounced"
        assert conn.execute(text("SELECT count(*) FROM suppression_list")).scalar() == 0
        assert conn.execute(text("SELECT state FROM leads WHERE id = :l"), {"l": lead.lead_id}).scalar() != "suppressed"


def test_a_dead_address_is_still_suppressed(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    msg = _bounce(
        lead, mid, clean_db, settings, cipher, "550 5.1.1 The email account that you tried to reach does not exist"
    )
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        assert handle_bounce(conn, settings.key("suppression_hash_key"), cipher, lead.tenant_id, msg) == [
            "bounce:hard",
            "suppressed",
        ]
        assert conn.execute(text("SELECT state FROM leads WHERE id = :l"), {"l": lead.lead_id}).scalar() == "suppressed"
