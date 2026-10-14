"""Replies safety net: a reply whose push notification was lost is still found and handled by the 15-minute sweep,
exactly once, even when it comes from an unknown sender (not stored) and even if a late push arrives too."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.adapters.inbound.base import InboundEmail
from app.core.crypto import PiiCipher
from app.db.session import DbContext, transaction
from app.demo.seed import providers, seed_demo
from app.workflows.inbound_sweep import sweep_inboxes
from app.workflows.pipeline import agent_context, process_inbound

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)
UNSUB = InboundEmail(
    provider_message_id="<lost-1@stranger.example>",
    from_email="someone@stranger.example",
    subject="Re: hello",
    body_text="Please unsubscribe me from your list.",
    received_at=NOW,
)


class FakeInbox:
    def __init__(self, listed: list[tuple[str, str]], messages: dict[str, InboundEmail]) -> None:
        self.listed, self.messages, self.fetched = listed, messages, []  # type: ignore[var-annotated]

    def recent_ids(self, since: datetime, limit: int = 200) -> list[tuple[str, str]]:
        assert since < NOW
        return self.listed

    def fetch_message(self, message_id: str) -> InboundEmail:
        self.fetched.append(message_id)
        return self.messages[message_id]


def _counts(engine, tenant_id):  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator"), engine) as conn:
        dpr = conn.execute(text("SELECT count(*) FROM data_protection_requests WHERE tenant_id = :t"), {"t": tenant_id})
        sup = conn.execute(text("SELECT count(*) FROM suppression_list"))
        return dpr.scalar_one(), sup.scalar_one()


def test_a_lost_push_is_recovered_once_and_a_late_push_changes_nothing(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)
    with transaction(DbContext(role="operator"), clean_db) as conn:
        conn.execute(text("UPDATE inboxes SET provider = 'msgraph'"))
    real = settings.model_copy(update={"email_adapter_mode": "sandbox"})
    cipher = PiiCipher(settings.key("pii_master_key"))
    inbox = FakeInbox([("g-1", UNSUB.provider_message_id), ("g-2", "")], {"g-1": UNSUB})
    before = _counts(clean_db, demo.tenant_id)

    first = sweep_inboxes(clean_db, real, cipher, now=NOW, inbox_factory=lambda *_: inbox)
    assert (first["recovered"], first["no_message_id"], first["failed_mailboxes"]) == (1, 1, 0)
    after = _counts(clean_db, demo.tenant_id)
    assert after == (before[0] + 1, before[1] + 1)  # the unsubscribe was honoured: suppressed, request opened

    second = sweep_inboxes(clean_db, real, cipher, now=NOW, inbox_factory=lambda *_: inbox)
    assert second["recovered"] == 0 and inbox.fetched == ["g-1"]  # recognised without fetching it again
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    process_inbound(ctx, [UNSUB], providers())  # the late push for the same message
    assert _counts(clean_db, demo.tenant_id) == after  # nothing handled twice

    with transaction(DbContext(role="operator"), clean_db) as conn:  # only a keyed hash is kept
        row = conn.execute(text("SELECT message_hash FROM inbound_seen")).scalar_one()
        assert b"stranger" not in bytes(row)
        recovered = conn.execute(
            text("SELECT after FROM audit_log WHERE action = 'inbound.sweep_recovered'")
        ).scalar_one()  # feeds the RepliesRecoveredBySweep alert
        assert recovered["count"] == 1 and "stranger" not in str(recovered)


def test_one_failing_mailbox_does_not_stop_the_sweep_and_mock_mode_skips(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    seed_demo(clean_db, settings)
    with transaction(DbContext(role="operator"), clean_db) as conn:
        conn.execute(text("UPDATE inboxes SET provider = 'google'"))
    cipher = PiiCipher(settings.key("pii_master_key"))

    def broken(*_: object) -> FakeInbox:
        raise ConnectionError("provider down")

    real = settings.model_copy(update={"email_adapter_mode": "sandbox"})
    r = sweep_inboxes(clean_db, real, cipher, now=NOW, inbox_factory=broken)
    assert r["failed_mailboxes"] == r["mailboxes"] >= 1
    assert "skipped" in sweep_inboxes(clean_db, settings, cipher, now=NOW, inbox_factory=broken)
