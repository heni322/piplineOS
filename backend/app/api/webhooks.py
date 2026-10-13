"""Provider webhooks. Microsoft Graph change notifications: validation handshake, then each notification is
authenticated by its per-mailbox clientState and handed to a worker (the webhook itself does no processing).
Gmail (via Pub/Sub push): the request carries a Google-signed OIDC token, verified before anything is queued."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import text

from app.adapters.google_auth import GoogleAuthError, PushVerifier
from app.adapters.inbound.gmail import parse_push
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


_verifiers: dict[tuple[str, str], PushVerifier] = {}


def _verifier(settings: Settings) -> PushVerifier | None:
    if not (settings.google_push_audience and settings.google_push_service_account):
        return None
    key = (settings.google_push_audience, settings.google_push_service_account)
    if key not in _verifiers:  # keeps Google's signing keys cached between requests
        _verifiers[key] = PushVerifier(*key)
    return _verifiers[key]


def _enqueue_gmail(mailbox: str, history_id: int) -> None:  # separated so tests can observe it
    from app.workflows.celery_app import gmail_history_task

    gmail_history_task.delay(mailbox, history_id)


def _reject(reason: str) -> None:
    with transaction(DbContext.system(), get_engine()) as conn:
        write_audit(
            conn,
            action="webhook.gmail.rejected",
            actor_type="system",
            actor_id="webhook",
            tenant_id=None,
            after={"reason": reason},
        )
    log.warning("gmail_push_rejected", reason=reason)


@router.post("/webhooks/google", include_in_schema=False)
async def gmail_push(request: Request, settings: Annotated[Settings, Depends(settings_dep)]) -> Response:
    verifier = _verifier(settings)
    if verifier is None:
        return Response(status_code=404)
    if len(await request.body()) > 64_000:
        return Response(status_code=413)
    try:
        verifier.verify(request.headers.get("authorization"))
    except GoogleAuthError as exc:
        _reject(str(exc))
        return Response(status_code=403)
    try:
        payload: dict[str, Any] = await request.json()
    except ValueError:
        return Response(status_code=400)
    parsed = parse_push(payload)
    if parsed is None:
        _reject("not a gmail notification")
        return Response(status_code=204)  # acknowledged so Pub/Sub doesn't retry it forever
    mailbox, history_id = parsed
    with transaction(DbContext(role="operator"), get_engine()) as conn:
        known = conn.execute(
            text("SELECT 1 FROM inboxes WHERE provider = 'google' AND lower(address) = lower(:a)"), {"a": mailbox}
        ).first()
    if known is None:
        _reject("unknown mailbox")
        return Response(status_code=204)
    _enqueue_gmail(mailbox, history_id)
    return Response(status_code=204)
