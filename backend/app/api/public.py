"""Public endpoints: one-click unsubscribe (RFC 8058). GET shows a confirmation page (link scanners prefetch GETs);
POST performs the unsubscribe. Always idempotent; always safe."""

from __future__ import annotations

import contextlib
import html
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.api.deps import settings_dep
from app.core.audit import write_audit
from app.db.session import DbContext, get_engine, transaction
from app.domain.leads.state_machine import TransitionError, record_objection
from app.domain.suppression import unsubscribe
from app.domain.suppression.service import RecipientHashes, suppress_domain, suppress_email
from app.settings import Settings

router = APIRouter(tags=["public"])

CSS = (
    "body{{font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;line-height:1.5;"
    "color:#1a1a1a}}button{{font:inherit;padding:.6rem 1.2rem;border-radius:.4rem;border:1px solid #1a1a1a;"
    "background:#1a1a1a;color:#fff;cursor:pointer}}"
)
PAGE = (
    '<!doctype html><html lang="en-GB"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<meta name="robots" content="noindex,nofollow"><title>{title}</title><style>' + CSS + "</style></head>"
    "<body><main><h1>{title}</h1>{body}</main></body></html>"
)


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(PAGE.format(title=html.escape(title), body=body), status_code=status)


@router.get("/u/{token}", response_class=HTMLResponse)
def unsubscribe_confirm(token: str, settings: Annotated[Settings, Depends(settings_dep)]) -> HTMLResponse:
    try:
        unsubscribe.verify(settings.key("unsubscribe_signing_key"), token)
    except unsubscribe.InvalidToken:
        return _page(
            "Link not valid",
            "<p>This unsubscribe link is not valid. Reply to the email with "
            "&ldquo;unsubscribe&rdquo; and we will remove you.</p>",
            400,
        )
    safe = html.escape(token, quote=True)
    return _page(
        "Unsubscribe",
        f'<p>Click below to stop all further emails from us.</p><form method="post" '
        f'action="/u/{safe}"><button type="submit">Unsubscribe</button></form>',
    )


@router.post("/u/{token}", response_class=HTMLResponse)
def unsubscribe_do(token: str, settings: Annotated[Settings, Depends(settings_dep)]) -> HTMLResponse:
    engine = get_engine()
    try:
        token_id = unsubscribe.verify(settings.key("unsubscribe_signing_key"), token)
    except unsubscribe.InvalidToken:
        with transaction(DbContext.system(), engine) as conn:
            write_audit(
                conn, action="unsubscribe.invalid_token", actor_type="system", actor_id="public", tenant_id=None
            )
        return _page(
            "Link not valid",
            "<p>This unsubscribe link is not valid. Reply to the email with "
            "&ldquo;unsubscribe&rdquo; and we will remove you.</p>",
            400,
        )
    with transaction(DbContext.system(), engine) as conn:
        found = conn.execute(text("SELECT * FROM unsubscribe_lookup(:id)"), {"id": token_id}).mappings().first()
    if found is None:
        return _page("Link not valid", "<p>This unsubscribe link is not valid.</p>", 400)
    tenant_id = found["tenant_id"]
    with transaction(DbContext.system(tenant_id), engine) as conn:
        tok = (
            conn.execute(text("SELECT * FROM unsubscribe_tokens WHERE id = :id FOR UPDATE"), {"id": token_id})
            .mappings()
            .one()
        )
        hashes = RecipientHashes(bytes(tok["recipient_hash"]), bytes(tok["recipient_domain_hash"]))
        suppress_email(conn, hashes, "unsubscribe", source_ref=f"unsubscribe_token:{token_id}")
        if tok["used_at"] is None:
            conn.execute(text("UPDATE unsubscribe_tokens SET used_at = now() WHERE id = :id"), {"id": token_id})
            with contextlib.suppress(TransitionError):
                record_objection(
                    conn,
                    tok["lead_id"],
                    actor_type="system",
                    actor_id="unsubscribe_link",
                    reason="one-click unsubscribe",
                )
            write_audit(
                conn,
                action="unsubscribe.completed",
                actor_type="system",
                actor_id="unsubscribe_link",
                tenant_id=tenant_id,
                entity_type="lead",
                entity_id=tok["lead_id"],
            )
    _ = suppress_domain  # domain-level suppression is an operator action, not triggered by one person's click
    return _page(
        "You have been unsubscribed",
        "<p>You will not receive further emails from us. This applies to every campaign we run.</p>",
    )
