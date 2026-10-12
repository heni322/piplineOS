"""Outbound gateway: dry-run by default, send-time re-check, deferral, caps, idempotency, crash safety."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.adapters.base import RateLimited
from app.adapters.email.mock import MockEmailSender
from app.db.session import DbContext, transaction
from app.domain.compliance.gate import run_gate
from app.domain.leads.state_machine import State, transition
from app.domain.outbound.gateway import OutboundGateway, reconcile_stuck_sends
from app.domain.outbound.messages import create_draft
from app.domain.suppression.service import RecipientHashes, suppress_email
from tests.factories import enable, footer_for, make_lead, make_operator, make_tenant

pytestmark = pytest.mark.db
IN_HOURS = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)  # Tue 11:00 BST
OUT_OF_HOURS = datetime(2026, 10, 13, 18, 30, tzinfo=UTC)  # Tue 19:30 BST


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    op = make_operator(clean_db, settings, cipher)
    enable(clean_db, op, "GB")
    return op


def approved_message(engine, settings, cipher, lead, *, step: int = 0):  # type: ignore[no-untyped-def]
    skey = settings.key("suppression_hash_key")
    footer = footer_for(engine, lead.tenant_id)
    with transaction(DbContext.system(lead.tenant_id), engine) as conn:
        if step == 0:
            _, d = run_gate(conn, tenant_id=lead.tenant_id, lead_id=lead.lead_id, cipher=cipher, suppression_key=skey)
            assert d.outcome == "allow", d.reason_codes
            transition(conn, lead.lead_id, State.PENDING_APPROVAL, actor_type="agent", actor_id="t", reason="t")
            transition(conn, lead.lead_id, State.APPROVED, actor_type="user", actor_id="t", reason="t")
        mid = create_draft(
            conn,
            cipher=cipher,
            suppression_key=skey,
            tenant_id=lead.tenant_id,
            lead_id=lead.lead_id,
            campaign_id=lead.campaign_id,
            inbox_id=lead.inbox_id,
            recipient_email=lead.email,
            subject="Hello",
            body="Hi there",
            footer=footer,
            sequence_step=step,
        )
        conn.execute(text("UPDATE messages SET status = 'approved' WHERE id = :m"), {"m": mid})
    return mid


def gw(engine, settings, cipher, at=IN_HOURS, **kw):  # type: ignore[no-untyped-def]
    return OutboundGateway(settings, engine, cipher, clock=lambda: at, **kw)


def test_default_is_dry_run_with_full_record(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    out = gw(clean_db, settings, cipher).send_message(lead.tenant_id, mid)
    assert (out.status, out.mode) == ("dry_run_sent", "dry_run")
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        m = conn.execute(
            text("SELECT status, mode_at_send, send_compliance_decision_id FROM messages WHERE id = :m"), {"m": mid}
        ).one()
        assert m.status == "dry_run_sent" and m.mode_at_send == "dry_run" and m.send_compliance_decision_id
        assert conn.execute(text("SELECT state FROM leads WHERE id = :l"), {"l": lead.lead_id}).scalar() == "contacted"
        log = conn.execute(text("SELECT payload_enc FROM dry_run_log WHERE entity_id = :m"), {"m": mid}).scalar_one()
        payload = cipher.decrypt(conn, lead.tenant_id, "dry_run_log.payload", log)
        assert "List-Unsubscribe-Post" in payload and "/u/" in payload and lead.email in payload
        stages = (
            conn.execute(
                text("SELECT stage FROM compliance_decisions WHERE lead_id = :l ORDER BY created_at"),
                {"l": lead.lead_id},
            )
            .scalars()
            .all()
        )
        assert stages == ["queue", "send"]


def test_resending_is_idempotent(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    g = gw(clean_db, settings, cipher)
    assert g.send_message(lead.tenant_id, mid).status == "dry_run_sent"
    again = g.send_message(lead.tenant_id, mid)
    assert again.status == "skipped" and again.reason_codes == ("ALREADY_DRY_RUN_SENT",)
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM dry_run_log")).scalar() == 1


def test_suppressed_after_approval_is_blocked_at_send(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        suppress_email(conn, RecipientHashes.for_email(settings.key("suppression_hash_key"), lead.email), "unsubscribe")
    out = gw(clean_db, settings, cipher).send_message(lead.tenant_id, mid)
    assert out.status == "blocked" and "SUPPRESSED_GLOBAL" in out.reason_codes
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        assert conn.execute(text("SELECT status FROM messages WHERE id = :m"), {"m": mid}).scalar() == "blocked_at_send"
        assert conn.execute(text("SELECT state FROM leads WHERE id = :l"), {"l": lead.lead_id}).scalar() == "suppressed"
        assert conn.execute(text("SELECT count(*) FROM dry_run_log")).scalar() == 0


def test_domain_suppression_blocks_everyone_at_that_domain(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    from app.domain.suppression.service import suppress_domain

    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        suppress_domain(
            conn, RecipientHashes.for_email(settings.key("suppression_hash_key"), lead.email).domain, "complaint"
        )
    assert gw(clean_db, settings, cipher).send_message(lead.tenant_id, mid).status == "blocked"


def test_rule_change_after_approval_is_caught_at_send(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    with transaction(DbContext(role="operator", user_id=world), clean_db) as conn:
        from app.domain.compliance.repository import enable_country

        enable_country(conn, "GB", world, enabled=False)
    out = gw(clean_db, settings, cipher).send_message(lead.tenant_id, mid)
    assert out.status == "blocked" and "COUNTRY_NOT_ENABLED" in out.reason_codes


def test_tenant_losing_dpa_stops_sending(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    with transaction(DbContext(role="operator"), clean_db) as conn:
        conn.execute(
            text("UPDATE tenants SET dpa_status = 'none', dpa_signed_at = NULL WHERE id = :t"), {"t": lead.tenant_id}
        )
    out = gw(clean_db, settings, cipher).send_message(lead.tenant_id, mid)
    assert out.status == "blocked" and "TENANT_NO_DPA" in out.reason_codes


def test_outside_business_hours_is_deferred_not_sent(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    out = gw(clean_db, settings, cipher, at=OUT_OF_HOURS).send_message(lead.tenant_id, mid)
    assert out.status == "deferred" and out.reason_codes == ("OUTSIDE_BUSINESS_HOURS",)
    assert out.scheduled_for == datetime(2026, 10, 14, 8, 0, tzinfo=UTC)  # Wed 09:00 BST
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        assert conn.execute(text("SELECT status FROM messages WHERE id = :m"), {"m": mid}).scalar() == "queued"
    assert gw(clean_db, settings, cipher).send_message(lead.tenant_id, mid).status == "dry_run_sent"


def test_inbox_daily_cap_defers(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    tenant = make_tenant(clean_db)
    lead = make_lead(clean_db, settings, cipher, tenant, inbox_cap=1)
    m1 = approved_message(clean_db, settings, cipher, lead, step=0)
    m2 = approved_message(clean_db, settings, cipher, lead, step=1)
    g = gw(clean_db, settings, cipher)
    assert g.send_message(tenant, m1).status == "dry_run_sent"
    out = g.send_message(tenant, m2)
    assert out.status == "deferred" and out.reason_codes == ("INBOX_DAILY_CAP",)


def test_retryable_provider_error_then_success_without_duplicate(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    sandbox = MockEmailSender(fail_with=RateLimited("429"))
    s2 = settings.model_copy(update={"email_adapter_mode": "sandbox"})
    g = OutboundGateway(s2, clean_db, cipher, sandbox_sender=sandbox, clock=lambda: IN_HOURS)
    assert g.send_message(lead.tenant_id, mid).status == "retry_later"
    sandbox.fail_with = None
    out = g.send_message(lead.tenant_id, mid)
    assert (out.status, out.mode) == ("sent", "sandbox")
    assert len(sandbox.sent) == 1


def test_crash_mid_send_is_never_resent(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    crashing = MockEmailSender(fail_with=ConnectionResetError("worker died mid-call"))
    s2 = settings.model_copy(update={"email_adapter_mode": "sandbox"})
    g = OutboundGateway(s2, clean_db, cipher, sandbox_sender=crashing, clock=lambda: IN_HOURS)
    with pytest.raises(ConnectionResetError):
        g.send_message(lead.tenant_id, mid)
    crashing.fail_with = None
    assert g.send_message(lead.tenant_id, mid).status == "skipped"  # stuck in 'sending': not retried
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        conn.execute(
            text("UPDATE messages SET sending_started_at = now() - interval '11 minutes' WHERE id = :m"), {"m": mid}
        )
    assert reconcile_stuck_sends(clean_db, lead.tenant_id, 10) == 1
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        assert (
            conn.execute(text("SELECT status FROM messages WHERE id = :m"), {"m": mid}).scalar()
            == "needs_reconciliation"
        )
        assert (
            conn.execute(text("SELECT reason FROM escalations WHERE lead_id = :l"), {"l": lead.lead_id}).scalar()
            == "needs_reconciliation"
        )
    assert crashing.sent == []
    assert g.send_message(lead.tenant_id, mid).status == "skipped"


def test_live_mode_requires_all_three_keys(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    live = MockEmailSender()
    tenant = make_tenant(clean_db, live_mode=True)
    lead = make_lead(clean_db, settings, cipher, tenant)
    mid = approved_message(clean_db, settings, cipher, lead)
    # adapter live + tenant live, but GLOBAL_GO_LIVE false and checklist unsigned -> dry run
    s_live = settings.model_copy(update={"email_adapter_mode": "live"})
    out = OutboundGateway(s_live, clean_db, cipher, live_sender=live, clock=lambda: IN_HOURS).send_message(tenant, mid)
    assert out.mode == "dry_run" and live.sent == []


def test_live_send_rejected_by_database_when_checklist_unsigned(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    """Even if application code wrongly resolved 'live', the DB trigger refuses because go-live isn't signed off."""
    live = MockEmailSender()
    tenant = make_tenant(clean_db, live_mode=True)
    lead = make_lead(clean_db, settings, cipher, tenant)
    mid = approved_message(clean_db, settings, cipher, lead)
    s_live = settings.model_copy(update={"email_adapter_mode": "live", "global_go_live": True})
    g = OutboundGateway(s_live, clean_db, cipher, live_sender=live, clock=lambda: IN_HOURS)
    import app.domain.outbound.gateway as gmod

    orig = gmod.resolve_mode
    gmod.resolve_mode = lambda **kw: "live"  # simulate a bug in mode resolution
    try:
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError, match="go-live checklist not fully signed off"):
            g.send_message(tenant, mid)
    finally:
        gmod.resolve_mode = orig
    with transaction(DbContext.system(tenant), clean_db) as conn:
        assert conn.execute(text("SELECT status FROM messages WHERE id = :m"), {"m": mid}).scalar() == "sending"
    # the message is now stuck in 'sending' and goes to human reconciliation; never auto-resent


def test_stale_decision_cannot_be_used(clean_db, world, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    slow = settings.model_copy(update={"email_adapter_mode": "sandbox"})

    class SlowSender(MockEmailSender):
        def send(self, payload):  # type: ignore[no-untyped-def, override]
            with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:  # age the decision by 16 minutes
                conn.execute(text("SELECT 1"))
            return super().send(payload)

    g = OutboundGateway(slow, clean_db, cipher, sandbox_sender=SlowSender(), clock=lambda: IN_HOURS)
    # Age the send decision artificially by patching suppression_checked_at before finalisation
    from sqlalchemy.exc import DBAPIError

    orig_dispatch = g._dispatch

    def aged_dispatch(tenant_id, message_id, p):  # type: ignore[no-untyped-def]
        res = orig_dispatch(tenant_id, message_id, p)
        with transaction(DbContext.system(tenant_id), clean_db) as conn:
            conn.execute(
                text("UPDATE messages SET suppression_checked_at = now() - interval '16 minutes' WHERE id = :m"),
                {"m": message_id},
            )
        return res

    g._dispatch = aged_dispatch  # type: ignore[method-assign]
    with pytest.raises(DBAPIError, match="suppression not checked recently"):
        g.send_message(lead.tenant_id, mid)
    _ = timedelta
