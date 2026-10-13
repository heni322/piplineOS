"""Provider webhooks. Microsoft Graph change notifications: validation handshake, then each notification is
authenticated by its per-mailbox clientState and handed to a worker (the webhook itself does no processing)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import text

from app.adapters.inbound.msgraph import mailbox_for
from app.api.deps import settings_dep
from app.core.audit import write_audit
from app.core.logging import get_logger
from app.db.session import DbContext, get_engine, transaction
from app.settings import Settings

router = APIRouter(tags=["webhooks"])
log = get_logger(__name__)


def _enqueue(mailbox: str, message_id: str) -> None:  # separated so tests can observe it
    from app.workflows.celery_app import graph_message_task

    graph_message_task.delay(mailbox, message_id)


@router.post("/webhooks/msgraph", include_in_schema=False)
async def msgraph(request: Request, settings: Annotated[Settings, Depends(settings_dep)]) -> Response:
    token = request.query_params.get("validationToken")
    if token is not None:  # subscription validation: echo within 10 s, as plain text
        return PlainTextResponse(token[:1024])
    if settings.msgraph_webhook_secret is None:
        return Response(status_code=404)
    body = await request.body()
    if len(body) > 256_000:
        return Response(status_code=413)
    try:
        payload: dict[str, Any] = await request.json()
    except ValueError:
        return Response(status_code=400)
    secret = settings.msgraph_webhook_secret.get_secret_value().encode()
    with transaction(DbContext(role="operator"), get_engine()) as conn:
        mailboxes = list(conn.execute(text("SELECT address FROM inboxes WHERE provider = 'msgraph'")).scalars())
    rejected = 0
    for n in payload.get("value", [])[:100]:
        mb = mailbox_for(n.get("clientState"), secret, mailboxes)
        msg_id = (n.get("resourceData") or {}).get("id")
        if mb is None or not msg_id or n.get("changeType") != "created":
            rejected += 1
            continue
        _enqueue(mb, str(msg_id))
    if rejected:
        with transaction(DbContext.system(), get_engine()) as conn:
            write_audit(
                conn,
                action="webhook.msgraph.rejected",
                actor_type="system",
                actor_id="webhook",
                tenant_id=None,
                after={"count": rejected},
            )
        log.warning("msgraph_notifications_rejected", count=rejected)
    return Response(status_code=202)
