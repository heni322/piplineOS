"""Emergency stop and Pilot Mode: a pause HOLDS work (never rejects or cancels it) and resuming continues; the pilot
limit caps how many new people a campaign emails while follow-ups continue; everything is audited."""

from __future__ import annotations

import dataclasses
import uuid

import pytest
from sqlalchemy import text

from app.db.session import DbContext, transaction
from app.domain.tenants import controls
from app.workflows.pipeline import send_due
from tests.factories import enable, make_lead, make_operator, make_tenant
from tests.integration.test_gateway import IN_HOURS, approved_message, gw

pytestmark = pytest.mark.db


@pytest.fixture
def op(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    o = make_operator(clean_db, settings, cipher)
    enable(clean_db, o, "GB")
    return o


def _tenant(engine) -> uuid.UUID:  # type: ignore[no-untyped-def]
    from app.demo.dataset import DEMO_CONFIG
    from app.domain.tenants.config import save_config

    t = make_tenant(engine)
    with transaction(DbContext(role="operator"), engine) as conn:
        save_config(conn, t, DEMO_CONFIG, None)
    return t


def _one(engine, tenant_id, sql, **p):  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(tenant_id), engine) as conn:
        return conn.execute(text(sql), p).scalar()


def _operator(engine, op, fn, **kw):  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator", user_id=op), engine) as conn:
        return fn(conn, operator_id=op, **kw)


def test_client_pause_holds_and_resume_sends(clean_db, op, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, _tenant(clean_db))
    mid = approved_message(clean_db, settings, cipher, lead)
    _operator(clean_db, op, controls.pause_client, tenant_id=lead.tenant_id, reason="client asked to stop")

    out = gw(clean_db, settings, cipher).send_message(lead.tenant_id, mid)
    assert out.status == "blocked" and out.reason_codes == ("CLIENT_PAUSED",)
    # Held, not destroyed: the message is still queued, the lead still approved, no compliance rejection recorded.
    assert _one(clean_db, lead.tenant_id, "SELECT status FROM messages WHERE id = :m", m=mid) == "queued"
    assert _one(clean_db, lead.tenant_id, "SELECT state FROM leads WHERE id = :l", l=lead.lead_id) == "approved"
    assert _one(clean_db, lead.tenant_id, "SELECT count(*) FROM compliance_decisions WHERE stage = 'send'") == 0
    # The scheduler does not even pick held work up.
    assert send_due(clean_db, settings, cipher, lead.tenant_id, now=IN_HOURS) == []

    _operator(clean_db, op, controls.resume_client, tenant_id=lead.tenant_id)
    sent = send_due(clean_db, settings, cipher, lead.tenant_id, now=IN_HOURS)
    assert [o.status for o in sent] == ["dry_run_sent"]
    actions = (
        _one(
            clean_db,
            lead.tenant_id,
            "SELECT string_agg(action, ',' ORDER BY created_at) FROM audit_log WHERE action LIKE 'tenant.%'",
        )
        or ""
    )
    assert actions.split(",") == ["tenant.paused", "tenant.resumed"]


def test_pause_needs_a_reason_and_an_active_client(clean_db, op) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant(clean_db)
    with pytest.raises(controls.ControlError, match="REASON_REQUIRED"):
        _operator(clean_db, op, controls.pause_client, tenant_id=t, reason="  ")
    onboarding = make_tenant(clean_db, status="onboarding")
    with pytest.raises(controls.ControlError, match="NOT_ACTIVE"):
        _operator(clean_db, op, controls.pause_client, tenant_id=onboarding, reason="x")
    with pytest.raises(controls.ControlError, match="NOT_PAUSED"):
        _operator(clean_db, op, controls.resume_client, tenant_id=t)


def test_campaign_pause_holds_only_that_campaign(clean_db, op, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    t = _tenant(clean_db)
    a, b = make_lead(clean_db, settings, cipher, t), make_lead(clean_db, settings, cipher, t)
    ma, mb = approved_message(clean_db, settings, cipher, a), approved_message(clean_db, settings, cipher, b)
    _operator(clean_db, op, controls.pause_campaign, campaign_id=a.campaign_id, reason="bad copy")
    out = send_due(clean_db, settings, cipher, t, now=IN_HOURS)
    assert [o.status for o in out] == ["dry_run_sent"]
    assert _one(clean_db, t, "SELECT status FROM messages WHERE id = :m", m=ma) == "approved"
    assert _one(clean_db, t, "SELECT status FROM messages WHERE id = :m", m=mb) == "dry_run_sent"
    # The gateway itself also holds it (defence in depth for any other caller).
    assert gw(clean_db, settings, cipher).send_message(t, ma).reason_codes == ("CAMPAIGN_PAUSED",)
    _operator(clean_db, op, controls.resume_campaign, campaign_id=a.campaign_id)
    assert [o.status for o in send_due(clean_db, settings, cipher, t, now=IN_HOURS)] == ["dry_run_sent"]


def test_pilot_limit_holds_new_contacts_but_not_follow_ups(clean_db, op, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    t = _tenant(clean_db)
    first = make_lead(clean_db, settings, cipher, t)
    second = make_lead(clean_db, settings, cipher, t)
    with transaction(DbContext.system(t), clean_db) as conn:  # both leads in the first lead's campaign
        conn.execute(
            text("UPDATE leads SET campaign_id = :c WHERE id = :l"), {"c": first.campaign_id, "l": second.lead_id}
        )
    second = dataclasses.replace(second, campaign_id=first.campaign_id)
    usage = _operator(clean_db, op, controls.set_pilot_limit, campaign_id=first.campaign_id, max_contacts=1)
    assert (usage.limit, usage.used, usage.reached) == (1, 0, False)

    m1 = approved_message(clean_db, settings, cipher, first)
    m2 = approved_message(clean_db, settings, cipher, second)
    g = gw(clean_db, settings, cipher)
    assert g.send_message(t, m1).status == "dry_run_sent"
    held = g.send_message(t, m2)
    assert held.status == "blocked" and held.reason_codes == ("PILOT_LIMIT_REACHED",)
    assert _one(clean_db, t, "SELECT status FROM messages WHERE id = :m", m=m2) == "queued"
    assert _one(clean_db, t, "SELECT state FROM leads WHERE id = :l", l=second.lead_id) == "approved"

    # A follow-up to the person already contacted still goes out.
    f1 = approved_message(clean_db, settings, cipher, first, step=1)
    assert g.send_message(t, f1).status == "dry_run_sent"

    # Raising the limit (audited) releases the held first email.
    usage = _operator(clean_db, op, controls.set_pilot_limit, campaign_id=first.campaign_id, max_contacts=2)
    assert (usage.used, usage.reached) == (1, False)
    assert g.send_message(t, m2).status == "dry_run_sent"
    assert _one(clean_db, t, "SELECT count(*) FROM audit_log WHERE action = 'campaign.pilot_limit_set'") == 2


def test_new_campaigns_start_in_pilot_mode(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, _tenant(clean_db))
    assert (
        _one(clean_db, lead.tenant_id, "SELECT pilot_max_contacts FROM campaigns WHERE id = :c", c=lead.campaign_id)
        == 100
    )


def test_pilot_limit_range(clean_db, op, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead = make_lead(clean_db, settings, cipher, _tenant(clean_db))
    for bad in (0, controls.PILOT_MAX + 1):
        with pytest.raises(controls.ControlError, match="OUT_OF_RANGE"):
            _operator(clean_db, op, controls.set_pilot_limit, campaign_id=lead.campaign_id, max_contacts=bad)
    with pytest.raises(controls.ControlError, match="NOT_FOUND"):
        _operator(clean_db, op, controls.set_pilot_limit, campaign_id=uuid.uuid4(), max_contacts=5)
    assert (
        _operator(clean_db, op, controls.set_pilot_limit, campaign_id=lead.campaign_id, max_contacts=None).limit is None
    )


def test_paused_client_leads_wait_in_the_pipeline(clean_db, op, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    from app.workflows.pipeline import Providers, agent_context, process_lead

    lead = make_lead(clean_db, settings, cipher, _tenant(clean_db))
    _operator(clean_db, op, controls.pause_client, tenant_id=lead.tenant_id, reason="emergency")
    ctx = agent_context(clean_db, settings, cipher, lead.tenant_id, lead.campaign_id)
    # Providers are never touched while paused: passing None proves no enrichment or AI step runs.
    assert process_lead(ctx, lead.lead_id, Providers([], None, None, None)) == "enriched"  # type: ignore[arg-type]
    assert _one(clean_db, lead.tenant_id, "SELECT count(*) FROM compliance_decisions") == 0
