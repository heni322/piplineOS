"""The compliance gate cannot be bypassed from any code path — not even raw SQL as the application role."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import DbContext, transaction
from app.domain.compliance.gate import run_gate
from app.domain.outbound.messages import create_draft
from tests.factories import enable, footer_for, make_lead, make_operator, make_tenant

pytestmark = pytest.mark.db


@pytest.fixture
def msg(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    op = make_operator(clean_db, settings, cipher)
    enable(clean_db, op, "GB")
    lead = make_lead(clean_db, settings, cipher, make_tenant(clean_db), state="approved")
    footer = footer_for(clean_db, lead.tenant_id)
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        mid = create_draft(
            conn,
            cipher=cipher,
            suppression_key=settings.key("suppression_hash_key"),
            tenant_id=lead.tenant_id,
            lead_id=lead.lead_id,
            campaign_id=lead.campaign_id,
            inbox_id=lead.inbox_id,
            recipient_email=lead.email,
            subject="s",
            body="b",
            footer=footer,
        )
    return lead, mid


def _raw(engine, tenant_id, sql, **params):  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(tenant_id), engine) as conn:
        conn.execute(text(sql), params)


def test_cannot_mark_sent_directly(clean_db, msg) -> None:  # type: ignore[no-untyped-def]
    lead, mid = msg
    with pytest.raises(DBAPIError, match="must pass through sending"):
        _raw(
            clean_db,
            lead.tenant_id,
            "UPDATE messages SET status = 'dry_run_sent', mode_at_send = 'dry_run' WHERE id = :m",
            m=mid,
        )


def test_cannot_insert_a_sent_message(clean_db, msg, settings) -> None:  # type: ignore[no-untyped-def]
    lead, _ = msg
    with pytest.raises(DBAPIError, match="cannot be created in status"):
        _raw(
            clean_db,
            lead.tenant_id,
            """INSERT INTO messages (tenant_id, lead_id, campaign_id, recipient_hash,
             recipient_domain_hash, status, idempotency_key) VALUES (:t, :l, :c, '\\x00', '\\x00', 'sent', 'k')""",
            t=lead.tenant_id,
            l=lead.lead_id,
            c=lead.campaign_id,
        )


def test_sending_without_a_send_decision_is_refused(clean_db, msg) -> None:  # type: ignore[no-untyped-def]
    lead, mid = msg
    _raw(
        clean_db,
        lead.tenant_id,
        "UPDATE messages SET status = 'sending', suppression_checked_at = now() WHERE id = :m",
        m=mid,
    )
    with pytest.raises(DBAPIError, match="no fresh send-time allow decision"):
        _raw(
            clean_db,
            lead.tenant_id,
            "UPDATE messages SET status = 'dry_run_sent', mode_at_send = 'dry_run' WHERE id = :m",
            m=mid,
        )


def test_queue_time_decision_cannot_be_reused_for_sending(clean_db, msg, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    lead, mid = msg
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        conn.execute(text("UPDATE leads SET state = 'enriched' WHERE id = :l"), {"l": lead.lead_id})
        decision_id, d = run_gate(
            conn,
            tenant_id=lead.tenant_id,
            lead_id=lead.lead_id,
            cipher=cipher,
            suppression_key=settings.key("suppression_hash_key"),
        )
        assert d.outcome == "allow"
    _raw(
        clean_db,
        lead.tenant_id,
        "UPDATE messages SET status = 'sending', suppression_checked_at = now(), "
        "send_compliance_decision_id = :d WHERE id = :m",
        d=decision_id,
        m=mid,
    )
    with pytest.raises(DBAPIError, match="no fresh send-time allow decision"):
        _raw(
            clean_db,
            lead.tenant_id,
            "UPDATE messages SET status = 'dry_run_sent', mode_at_send = 'dry_run' WHERE id = :m",
            m=mid,
        )


def test_forged_allow_decision_still_blocked_for_suppressed_recipient(clean_db, msg, settings) -> None:  # type: ignore[no-untyped-def]
    from app.domain.suppression.service import RecipientHashes, suppress_email

    lead, mid = msg
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        did = conn.execute(
            text("""INSERT INTO compliance_decisions (tenant_id, lead_id, message_id, stage, outcome,
              reason_codes, rules_applied, suppression_checked_at, engine_version)
              VALUES (:t, :l, :m, 'send', 'allow', '{OK}', '{}', now(), 'forged') RETURNING id"""),
            {"t": lead.tenant_id, "l": lead.lead_id, "m": mid},
        ).scalar_one()
        suppress_email(conn, RecipientHashes.for_email(settings.key("suppression_hash_key"), lead.email), "objection")
        conn.execute(
            text(
                "UPDATE messages SET status = 'sending', suppression_checked_at = now(), "
                "send_compliance_decision_id = :d WHERE id = :m"
            ),
            {"d": did, "m": mid},
        )
    with pytest.raises(DBAPIError, match="recipient is suppressed"):
        _raw(
            clean_db,
            lead.tenant_id,
            "UPDATE messages SET status = 'dry_run_sent', mode_at_send = 'dry_run' WHERE id = :m",
            m=mid,
        )


def test_lead_cannot_be_marked_contacted_without_a_sent_message(clean_db, msg) -> None:  # type: ignore[no-untyped-def]
    lead, _ = msg
    with pytest.raises(DBAPIError, match="contacted requires a message"):
        _raw(clean_db, lead.tenant_id, "UPDATE leads SET state = 'contacted' WHERE id = :l", l=lead.lead_id)


def test_erased_is_absorbing(clean_db, msg) -> None:  # type: ignore[no-untyped-def]
    lead, _ = msg
    _raw(clean_db, lead.tenant_id, "UPDATE leads SET state = 'erased' WHERE id = :l", l=lead.lead_id)
    with pytest.raises(DBAPIError, match="erased is absorbing"):
        _raw(clean_db, lead.tenant_id, "UPDATE leads SET state = 'discovered' WHERE id = :l", l=lead.lead_id)


def test_sent_message_cannot_be_reset_for_resending(clean_db, msg, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from app.domain.outbound.gateway import OutboundGateway

    lead, mid = msg
    with transaction(DbContext.system(lead.tenant_id), clean_db) as conn:
        conn.execute(text("UPDATE messages SET status = 'approved' WHERE id = :m"), {"m": mid})
    out = OutboundGateway(
        settings, clean_db, cipher, clock=lambda: datetime(2026, 10, 13, 10, tzinfo=UTC)
    ).send_message(lead.tenant_id, mid)
    assert out.status == "dry_run_sent"
    with pytest.raises(DBAPIError, match="cannot go back"):
        _raw(clean_db, lead.tenant_id, "UPDATE messages SET status = 'approved' WHERE id = :m", m=mid)


def test_app_cannot_sign_off_golive_items(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    with (
        pytest.raises(DBAPIError, match="permission denied"),
        transaction(DbContext(role="operator"), clean_db) as conn,
    ):
        conn.execute(text("UPDATE golive_checklist_items SET signed_off_at = now()"))
    item = None
    with transaction(DbContext(role="operator"), clean_db) as conn:
        item = conn.execute(text("SELECT id FROM golive_checklist_items LIMIT 1")).scalar_one()
    # system / tenant contexts cannot use the sign-off function
    with pytest.raises(DBAPIError, match="only an active operator"), transaction(DbContext.system(), clean_db) as conn:
        conn.execute(text("SELECT golive_sign_off(:i, NULL)"), {"i": item})
    # an operator WITHOUT MFA cannot either
    import uuid

    from app.core.crypto import blind_index
    from app.core.security import hash_password

    uid = uuid.uuid4()
    with transaction(DbContext(role="operator", user_id=uid), clean_db) as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email_bidx, email_enc, password_hash, role) VALUES "
                "(:id, :b, '\\x00', :p, 'operator')"
            ),
            {
                "id": uid,
                "b": blind_index(settings.key("blind_index_key"), "nomfa@x.example", None),
                "p": hash_password("operator-password-1"),
            },
        )
    with (
        pytest.raises(DBAPIError, match="only an active operator"),
        transaction(DbContext(role="operator", user_id=uid), clean_db) as conn,
    ):
        conn.execute(text("SELECT golive_sign_off(:i, NULL)"), {"i": item})


def test_mfa_operator_can_sign_off_once(clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    op = make_operator(clean_db, settings, cipher)
    with transaction(DbContext(role="operator", user_id=op), clean_db) as conn:
        item = conn.execute(text("SELECT id FROM golive_checklist_items LIMIT 1")).scalar_one()
        conn.execute(text("SELECT golive_sign_off(:i, 'https://evidence.example')"), {"i": item})
        assert conn.execute(text("SELECT golive_complete('uk')")).scalar() is False  # 4 items still open
    with (
        pytest.raises(DBAPIError, match="already signed off"),
        transaction(DbContext(role="operator", user_id=op), clean_db) as conn,
    ):
        conn.execute(text("SELECT golive_sign_off(:i, NULL)"), {"i": item})
