"""Public endpoints: one-click unsubscribe (RFC 8058) and the prospect's reschedule link. GET only ever shows a page
(link scanners prefetch GETs); POST performs the action. Unsubscribe is always idempotent and always safe; a
reschedule link is single-use, offers only freshly computed free slots and goes through the calendar gateway."""

from __future__ import annotations

import contextlib
import html
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.api.deps import cipher_dep, settings_dep
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.db.session import DbContext, get_engine, transaction
from app.domain.leads.state_machine import TransitionError, record_objection
from app.domain.meetings import service as meetings
from app.domain.outbound.calendar import CalendarGateway
from app.domain.suppression import unsubscribe
from app.domain.suppression.service import RecipientHashes, suppress_domain, suppress_email
from app.domain.tenants.config import load_config
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


def _limited(request: Request, settings: Settings, what: str) -> bool:
    from app.core import ratelimit

    ip = request.client.host if request.client else "unknown"
    prefix = "test:" if settings.environment == "test" else ""
    return not ratelimit.allow(f"{prefix}{what}:{ip}", rate_per_sec=0.5, capacity=30, redis_url=settings.redis_url)


TOO_MANY = "<p>Too many requests from your network. Wait a minute and try again.</p>"


@router.post("/u/{token}", response_class=HTMLResponse)
def unsubscribe_do(token: str, request: Request, settings: Annotated[Settings, Depends(settings_dep)]) -> HTMLResponse:
    if _limited(request, settings, "unsub"):
        return _page("Please wait", TOO_MANY, 429)
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


# ------------------------------------------------------------------------------------------- reschedule link
RESCHEDULE_INVALID = (
    "<p>This link is no longer valid. Reply to your confirmation email and we will find another time.</p>"
)


def _reschedule_context(
    token: str, settings: Settings, cipher: PiiCipher, now: datetime
) -> tuple[uuid.UUID, uuid.UUID, list[meetings.Slot], str] | None:
    """Returns (tenant_id, meeting_id, offered slots, prospect timezone) or None when the link cannot be used."""
    try:
        token_id = unsubscribe.verify(settings.key("unsubscribe_signing_key"), token, unsubscribe.RESCHEDULE)
    except unsubscribe.InvalidToken:
        return None
    engine = get_engine()
    with transaction(DbContext.system(), engine) as conn:
        found = conn.execute(text("SELECT * FROM reschedule_lookup(:id)"), {"id": token_id}).mappings().first()
    if found is None or found["used_at"] is not None:
        return None
    tenant_id, meeting_id = found["tenant_id"], found["meeting_id"]
    with transaction(DbContext.system(tenant_id), engine) as conn:
        m = conn.execute(text("SELECT * FROM meetings WHERE id = :m"), {"m": meeting_id}).mappings().first()
        if m is None or m["status"] not in ("booked", "confirmed") or m["starts_at"] <= now:
            return None
        _, cfg = load_config(conn, tenant_id)
        prospect = meetings.load_prospect(conn, cipher, tenant_id, m["lead_id"])
        horizon = now + timedelta(days=cfg.calendar.horizon_business_days * 2 + 4)
        busy = CalendarGateway(settings, cipher).busy(cfg.calendar.calendar_ref, now, horizon)
        busy += [
            meetings.Slot(r.starts_at, r.ends_at)
            for r in conn.execute(
                text("SELECT starts_at, ends_at FROM meetings WHERE status IN ('booked','confirmed') AND id <> :m"),
                {"m": meeting_id},
            )
        ]
        current = meetings.Slot(m["starts_at"], m["ends_at"])
        slots = meetings.candidate_slots(cfg, prospect, now, busy, exclude=[current])
    return tenant_id, meeting_id, slots, prospect.tz


@router.get("/r/{token}", response_class=HTMLResponse)
def reschedule_page(
    token: str,
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
    cipher: Annotated[PiiCipher, Depends(cipher_dep)],
) -> HTMLResponse:
    if _limited(request, settings, "resched"):
        return _page("Please wait", TOO_MANY, 429)
    ctx = _reschedule_context(token, settings, cipher, datetime.now(UTC))
    if ctx is None:
        return _page("Link not valid", RESCHEDULE_INVALID, 400)
    _, _, slots, tz = ctx
    if not slots:
        return _page("No free times", "<p>There are no free times right now. Reply to your confirmation email.</p>")
    safe = html.escape(token, quote=True)
    options = "".join(
        f'<p><label><input type="radio" name="slot" value="{html.escape(s.starts_at.isoformat(), quote=True)}"'
        f"{' checked' if i == 0 else ''}> {html.escape(meetings.label(s, tz))}</label></p>"
        for i, s in enumerate(slots)
    )
    return _page(
        "Choose a new time",
        f'<form method="post" action="/r/{safe}">{options}<button type="submit">Move my call</button></form>',
    )


@router.post("/r/{token}", response_class=HTMLResponse)
async def reschedule_do(
    token: str,
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
    cipher: Annotated[PiiCipher, Depends(cipher_dep)],
) -> HTMLResponse:
    """Parses the tiny urlencoded form itself (no multipart dependency), then does the DB work off the event loop."""
    if _limited(request, settings, "resched"):
        return _page("Please wait", TOO_MANY, 429)
    raw = await request.body()
    if len(raw) > 200:
        return _page("Time not available", "<p>That request was not understood.</p>", 400)
    slot = (parse_qs(raw.decode("utf-8", "replace")).get("slot") or [""])[0]
    return await run_in_threadpool(_reschedule_do, token, slot, settings, cipher)


def _reschedule_do(token: str, slot: str, settings: Settings, cipher: PiiCipher) -> HTMLResponse:
    now = datetime.now(UTC)
    ctx = _reschedule_context(token, settings, cipher, now)
    if ctx is None:
        return _page("Link not valid", RESCHEDULE_INVALID, 400)
    tenant_id, meeting_id, slots, tz = ctx
    chosen = next((s for s in slots if s.starts_at.isoformat() == slot), None)
    if chosen is None:  # only a freshly offered slot can be chosen; anything else is refused
        return _page("Time not available", "<p>That time is no longer available. Go back and pick another.</p>", 409)
    token_id = unsubscribe.verify(settings.key("unsubscribe_signing_key"), token, unsubscribe.RESCHEDULE)
    with transaction(DbContext.system(tenant_id), get_engine()) as conn:
        used = conn.execute(
            text("UPDATE reschedule_tokens SET used_at = now() WHERE id = :id AND used_at IS NULL RETURNING id"),
            {"id": token_id},
        ).first()
        if used is None:
            return _page("Link not valid", RESCHEDULE_INVALID, 400)
        _, cfg = load_config(conn, tenant_id)
        result = meetings.reschedule(
            conn,
            cipher,
            settings,
            cfg,
            CalendarGateway(settings, cipher),
            tenant_id=tenant_id,
            meeting_id=meeting_id,
            new_slot=chosen,
            now=now,
        )
    if result["status"] == "rescheduled":
        return _page(
            "Your call has moved",
            f"<p>Your call is now on {html.escape(meetings.label(chosen, tz))}. A new confirmation is on its way.</p>",
        )
    if result["status"] == "escalated":
        return _page("We will be in touch", "<p>A member of the team will contact you to find a time.</p>")
    return _page("Time not available", "<p>That time could not be booked. Reply to your confirmation email.</p>", 409)
