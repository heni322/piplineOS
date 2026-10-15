"""Safety net for replies. Real mailboxes deliver replies by push (Microsoft Graph change notifications, Gmail
Pub/Sub). A push can be lost: our endpoint down during a deploy, a subscription that lapsed, a Gmail history cursor
that became too old. A lost reply could be an unsubscribe or an objection, so it must never stay lost.

Every 15 minutes, for each real mailbox, this lists the Message-IDs received in the last `lookback_hours` (ids
and headers only), skips the ones already processed or sent by us, and runs Agent 4 on the rest exactly as the push
path would. Processing is idempotent by Message-ID, so a race with a late push is harmless. Anything recovered is
logged (counts only, no personal data) because it means a push went missing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import structlog
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.adapters.inbound.base import InboundEmail
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.db.session import DbContext, transaction
from app.domain.tenants.controls import LISTENING_STATUSES
from app.settings import Settings

log = structlog.get_logger("pipelineos.inbound_sweep")
PROVIDERS = ("msgraph", "google")


class SweepableInbox(Protocol):
    def recent_ids(self, since: datetime, limit: int = 200) -> list[tuple[str, str]]: ...

    def fetch_message(self, message_id: str) -> InboundEmail: ...


def default_inbox(settings: Settings, provider: str, mailbox: str) -> SweepableInbox:
    from app.domain.outbound.providers import google_credentials, graph_credentials

    if provider == "msgraph":
        from app.adapters.inbound.msgraph import GraphInbox

        return GraphInbox(graph_credentials(settings), mailbox)
    from app.adapters.inbound.gmail import GmailInbox

    return GmailInbox(google_credentials(settings), mailbox)


def sweep_inboxes(
    engine: Engine,
    settings: Settings,
    cipher: PiiCipher,
    *,
    now: datetime | None = None,
    lookback_hours: int = 72,
    inbox_factory: Callable[[Settings, str, str], SweepableInbox] = default_inbox,
) -> dict[str, Any]:
    if settings.email_adapter_mode == "mock":
        return {"skipped": "mock mailboxes have no push to miss"}
    from app.adapters.registry import providers_for
    from app.domain.outbound.providers import calendar_gateway
    from app.domain.replies.service import message_hash
    from app.workflows.pipeline import agent_context, process_inbound

    now = now or datetime.now(UTC)
    since = now - timedelta(hours=lookback_hours)
    with transaction(DbContext(role="operator"), engine) as conn:
        boxes = conn.execute(
            text("""SELECT i.tenant_id, i.address, i.provider,
                      (SELECT c.id FROM campaigns c WHERE c.tenant_id = i.tenant_id
                       ORDER BY c.created_at LIMIT 1) AS cid
                    FROM inboxes i JOIN tenants t ON t.id = i.tenant_id
                    WHERE t.status = ANY(:s) AND i.provider = ANY(:p) ORDER BY i.address"""),
            {"p": list(PROVIDERS), "s": list(LISTENING_STATUSES)},
        ).all()
    totals = {"mailboxes": 0, "listed": 0, "recovered": 0, "no_message_id": 0, "failed_mailboxes": 0}
    for box in boxes:
        if box.cid is None:
            continue
        totals["mailboxes"] += 1
        try:
            inbox = inbox_factory(settings, box.provider, box.address)
            listed = inbox.recent_ids(since)
            totals["listed"] += len(listed)
            ids = [mid for _, mid in listed if mid]
            totals["no_message_id"] += sum(1 for _, mid in listed if not mid)
            key = settings.key("blind_index_key")
            hashes = {mid: message_hash(key, mid, box.tenant_id) for mid in ids}
            with transaction(DbContext.system(box.tenant_id), engine) as conn:
                known = set(
                    conn.execute(
                        text("""SELECT provider_message_id FROM replies WHERE provider_message_id = ANY(:ids)
                                UNION
                                SELECT provider_message_id FROM messages WHERE provider_message_id = ANY(:ids)"""),
                        {"ids": ids},
                    ).scalars()
                )
                seen = set(
                    conn.execute(
                        text("SELECT message_hash FROM inbound_seen WHERE message_hash = ANY(:h)"),
                        {"h": list(hashes.values())},
                    ).scalars()
                )
            known |= {mid for mid, h in hashes.items() if bytes(h) in {bytes(x) for x in seen}}
            missing = [gid for gid, mid in listed if mid and mid not in known]
            if not missing:
                continue
            msgs = [inbox.fetch_message(gid) for gid in missing]
            ctx = agent_context(engine, settings, cipher, box.tenant_id, box.cid)
            process_inbound(
                ctx, msgs, providers_for(settings, ctx), calendar_gateway(settings, cipher, box.address, box.provider)
            )
            totals["recovered"] += len(msgs)
            log.warning("inbound_sweep_recovered", tenant_id=str(box.tenant_id), count=len(msgs))
            with transaction(DbContext.system(box.tenant_id), engine) as conn:  # feeds the RepliesRecovered alert
                write_audit(
                    conn,
                    action="inbound.sweep_recovered",
                    actor_type="system",
                    actor_id="inbound_sweep",
                    tenant_id=box.tenant_id,
                    after={"count": len(msgs), "provider": box.provider},
                )
        except Exception as exc:  # one mailbox failing must not stop the others
            totals["failed_mailboxes"] += 1
            log.error("inbound_sweep_failed", tenant_id=str(box.tenant_id), error=type(exc).__name__)
    return totals
