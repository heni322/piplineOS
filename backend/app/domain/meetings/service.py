"""Booking and show-rate workflows: real availability (host calendar + both parties' business hours and bank
holidays), slot offers with re-offer if a slot is taken, idempotent booking via the outbound calendar gateway,
confirmation with agenda and invite, reminders, pre-meeting brief, reschedule (limited), attendance."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import RowMapping, text
from sqlalchemy.engine import Connection

from app.adapters.calendar.base import Slot
from app.core import i18n
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.domain.deliverability.hours import DEFAULT_TZ, is_business_time, is_holiday
from app.domain.leads.state_machine import State, get_lead, transition
from app.domain.outbound.calendar import BookingBlocked, CalendarGateway
from app.domain.outbound.messages import create_draft
from app.domain.outbound.tenant_footer import tenant_footer
from app.domain.suppression import unsubscribe
from app.domain.tenants.config import TenantConfig
from app.settings import Settings

__all__ = ["Prospect", "Slot"]  # Slot re-exported so workflows/api need not import the adapter layer


@dataclass
class Prospect:
    first_name: str
    email: str
    country: str
    subdivision: str | None
    tz: str


def label(slot: Slot, tz: str, language: str | None = None) -> str:
    """'Thursday 15 October at 14:00 (UK time)' / 'jeudi 15 octobre à 14h00 (heure de Paris)'."""
    return i18n.date_label(slot.starts_at.astimezone(ZoneInfo(tz)), tz, language)


def candidate_slots(
    cfg: TenantConfig,
    prospect: Prospect,
    now: datetime,
    busy: list[Slot],
    count: int | None = None,
    exclude: list[Slot] | None = None,
) -> list[Slot]:
    """Free slots on distinct days: inside the host's window, a business day for BOTH parties, inside the
    prospect's business hours, after the minimum notice and within the horizon."""
    cal = cfg.calendar
    host = ZoneInfo(cal.host_timezone)
    want = count or cal.slots_to_offer
    day = now.astimezone(host).date()
    out: list[Slot] = []
    business_day = 0
    while len(out) < want:
        day += timedelta(days=1)
        if day.weekday() >= 5 or is_holiday(day, cal.host_country, cal.host_subdivision):
            continue
        business_day += 1  # 1 = the next host business day
        if business_day > cal.horizon_business_days:
            break
        if business_day < max(1, cal.min_notice_business_days) or is_holiday(
            day, prospect.country, prospect.subdivision
        ):
            continue
        prefer = [cal.day_start_hour, 14] if len(out) % 2 == 0 else [14, cal.day_start_hour]
        starts = [(h, mi) for h in prefer for mi in (0,)] + [
            (h, mi)
            for h in range(cal.day_start_hour, cal.day_end_hour)
            for mi in (0, 30)
            if (h, mi) not in [(x, 0) for x in prefer]
        ]
        for h, minute in starts:
            start = datetime.combine(day, time(h, minute), tzinfo=host).astimezone(UTC)
            slot = Slot(start, start + timedelta(minutes=cal.meeting_minutes))
            if (
                slot.ends_at.astimezone(host).time() > time(cal.day_end_hour, 0)
                and slot.ends_at.astimezone(host).date() == day
            ):
                continue
            if any(slot.overlaps(b) for b in busy) or any(slot.overlaps(e) for e in exclude or []):
                continue
            if not (
                is_business_time(slot.starts_at, prospect.country, prospect.subdivision)
                and is_business_time(slot.ends_at - timedelta(minutes=1), prospect.country, prospect.subdivision)
            ):
                continue
            out.append(slot)
            break
    return out


def load_prospect(conn: Connection, cipher: PiiCipher, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> Prospect:
    r = (
        conn.execute(
            text(
                "SELECT p.name_enc, p.email_enc, p.country_code, p.subdivision_code FROM leads l JOIN prospects p "
                "ON p.id = l.prospect_id WHERE l.id = :l"
            ),
            {"l": lead_id},
        )
        .mappings()
        .one()
    )
    country = r["country_code"] or "GB"
    return Prospect(
        cipher.decrypt(conn, tenant_id, "prospects.name", r["name_enc"]).split()[0],
        cipher.decrypt(conn, tenant_id, "prospects.email", r["email_enc"]),
        country,
        r["subdivision_code"],
        DEFAULT_TZ.get(country, "Europe/London"),
    )


def template_message(
    conn: Connection,
    cipher: PiiCipher,
    settings: Settings,
    cfg: TenantConfig,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    kind: str,
    subject: str,
    body: str,
    status: str,
    key: str,
    reply_id: uuid.UUID | None = None,
    meeting_id: uuid.UUID | None = None,
    scheduled_for: datetime | None = None,
) -> uuid.UUID:
    p = load_prospect(conn, cipher, tenant_id, lead_id)
    campaign_id, inbox_id = conn.execute(
        text(
            "SELECT l.campaign_id, (SELECT id FROM inboxes WHERE status IN ('active',"
            "'warming') ORDER BY created_at LIMIT 1) FROM leads l WHERE l.id = :l"
        ),
        {"l": lead_id},
    ).one()
    mid = create_draft(
        conn,
        cipher=cipher,
        suppression_key=settings.key("suppression_hash_key"),
        tenant_id=tenant_id,
        lead_id=lead_id,
        campaign_id=campaign_id,
        inbox_id=inbox_id,
        recipient_email=p.email,
        subject=subject,
        body=body,
        footer=tenant_footer(conn, tenant_id, cfg, settings.public_base_url),
        language=cfg.language,
        prompt_version=f"template:{kind}",
        idempotency_key=key,
        kind=kind,
    )
    conn.execute(
        text(
            "UPDATE messages SET kind = :k, status = :s, idempotency_key = :key, reply_id = :r, meeting_id = :m, "
            "scheduled_for = :sf WHERE id = :id"
        ),
        {"k": kind, "s": status, "key": key, "r": reply_id, "m": meeting_id, "sf": scheduled_for, "id": mid},
    )
    return mid


def offer_slots(
    conn: Connection,
    cipher: PiiCipher,
    settings: Settings,
    cfg: TenantConfig,
    cal: CalendarGateway,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    reply_id: uuid.UUID | None,
    now: datetime,
    intro: str | None = None,
    exclude: list[Slot] | None = None,
) -> tuple[uuid.UUID, list[str]] | None:
    """Creates a slot offer + a templated reply (routed to approval like any AI-handled reply)."""
    p = load_prospect(conn, cipher, tenant_id, lead_id)
    horizon = now + timedelta(days=cfg.calendar.horizon_business_days * 2)
    slots = candidate_slots(cfg, p, now, cal.busy(cfg.calendar.calendar_ref, now, horizon), exclude=exclude)
    if not slots:
        return None
    lg = cfg.language
    labels = [label(s, p.tz, lg) for s in slots]
    conn.execute(
        text("UPDATE slot_offers SET status = 'superseded' WHERE lead_id = :l AND status = 'open'"), {"l": lead_id}
    )
    lines = "\n".join(i18n.t(lg, "slot_option", i=i, label=lab) for i, lab in enumerate(labels, 1))
    body = (
        f"{i18n.t(lg, 'greeting', name=p.first_name)}\n\n{intro or i18n.t(lg, 'slot_intro')}\n\n{lines}\n\n"
        f"{i18n.t(lg, 'slot_close', minutes=cfg.calendar.meeting_minutes)}\n\n{cfg.brand_voice.sender_name.split()[0]}"
        + (f"\n\n{cfg.ai_disclosure_text}" if cfg.ai_disclosure == "always" else "")
    )
    offer_id = uuid.uuid4()
    mid = template_message(
        conn,
        cipher,
        settings,
        cfg,
        tenant_id=tenant_id,
        lead_id=lead_id,
        kind="agent_reply",
        subject="Times for a short call",
        body=body,
        status="pending_approval",
        key=f"offer:{offer_id}",
        reply_id=reply_id,
    )
    conn.execute(
        text("""INSERT INTO slot_offers (id, tenant_id, lead_id, message_id, slots, expires_at)
          VALUES (:id, :t, :l, :m, CAST(:s AS jsonb), :exp)"""),
        {
            "id": offer_id,
            "t": tenant_id,
            "l": lead_id,
            "m": mid,
            "s": json.dumps(
                [
                    {"starts_at": s.starts_at.isoformat(), "ends_at": s.ends_at.isoformat(), "label": lab}
                    for s, lab in zip(slots, labels, strict=True)
                ]
            ),
            "exp": now + timedelta(days=cfg.calendar.offer_valid_days),
        },
    )
    return mid, labels


def open_offer(conn: Connection, lead_id: uuid.UUID, now: datetime) -> RowMapping | None:
    return (
        conn.execute(
            text(
                "SELECT * FROM slot_offers WHERE lead_id = :l AND status = 'open' AND expires_at > :n "
                "ORDER BY offered_at DESC LIMIT 1 FOR UPDATE"
            ),
            {"l": lead_id, "n": now},
        )
        .mappings()
        .first()
    )


def _slot(d: dict[str, str]) -> Slot:
    return Slot(datetime.fromisoformat(d["starts_at"]), datetime.fromisoformat(d["ends_at"]))


def book(
    conn: Connection,
    cipher: PiiCipher,
    settings: Settings,
    cfg: TenantConfig,
    cal: CalendarGateway,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    offer: RowMapping,
    index: int,
    now: datetime,
) -> dict[str, object]:
    """Books the chosen slot, or re-offers if it has been taken since the offer. Idempotent per offer+slot."""
    slots = offer["slots"]
    chosen = _slot(slots[index])
    existing = conn.execute(
        text("SELECT id FROM meetings WHERE idempotency_key = :k"), {"k": f"book:{offer['id']}:{index}"}
    ).scalar()
    if existing:
        return {"status": "already_booked", "meeting_id": existing}
    if cfg.calendar.status != "ok":
        return {"status": "calendar_disconnected"}
    if any(chosen.overlaps(b) for b in cal.busy(cfg.calendar.calendar_ref, chosen.starts_at, chosen.ends_at)):
        conn.execute(text("UPDATE slot_offers SET status = 'superseded' WHERE id = :o"), {"o": offer["id"]})
        re = offer_slots(
            conn,
            cipher,
            settings,
            cfg,
            cal,
            tenant_id=tenant_id,
            lead_id=lead_id,
            reply_id=None,
            now=now,
            intro=i18n.t(cfg.language, "slot_intro_taken"),
            exclude=[chosen],
        )
        return {"status": "reoffered", "message_id": re[0] if re else None}
    p = load_prospect(conn, cipher, tenant_id, lead_id)
    meeting_id = uuid.uuid4()
    conn.execute(
        text("""INSERT INTO meetings (id, tenant_id, lead_id, calendar_provider, starts_at, ends_at, prospect_tz,
          host_tz, status, booked_by_system, idempotency_key, agenda)
          VALUES (:id, :t, :l, :cp, :s, :e, :ptz, :htz, 'booked', true, :k, :ag)"""),
        {
            "id": meeting_id,
            "t": tenant_id,
            "l": lead_id,
            "cp": settings.calendar_adapter_mode,
            "s": chosen.starts_at,
            "e": chosen.ends_at,
            "ptz": p.tz,
            "htz": cfg.calendar.host_timezone,
            "k": f"book:{offer['id']}:{index}",
            "ag": cfg.show_rate.agenda,
        },
    )
    try:
        event_id, mode = cal.create_event(
            conn,
            tenant_id=tenant_id,
            lead_id=lead_id,
            meeting_id=meeting_id,
            calendar_ref=cfg.calendar.calendar_ref,
            slot=chosen,
            title=cfg.show_rate.meeting_title,
            organiser=cfg.brand_voice.sender_name,
            description=cfg.show_rate.agenda,
            idempotency_key=f"evt:{meeting_id}",
        )
    except BookingBlocked as exc:
        conn.execute(text("UPDATE meetings SET status = 'cancelled' WHERE id = :m"), {"m": meeting_id})
        return {"status": "blocked", "reasons": list(exc.reasons)}
    conn.execute(text("UPDATE meetings SET provider_event_id = :e WHERE id = :m"), {"e": event_id, "m": meeting_id})
    conn.execute(
        text("UPDATE slot_offers SET status = 'accepted', accepted_index = :i WHERE id = :o"),
        {"i": index, "o": offer["id"]},
    )
    transition(conn, lead_id, State.BOOKED, actor_type="system", actor_id="booking", reason=f"booked ({mode})")
    after_booking(
        conn,
        cipher,
        settings,
        cfg,
        tenant_id=tenant_id,
        lead_id=lead_id,
        meeting_id=meeting_id,
        slot=chosen,
        prospect=p,
        now=now,
    )
    return {"status": "booked", "meeting_id": meeting_id, "mode": mode}


def after_booking(
    conn: Connection,
    cipher: PiiCipher,
    settings: Settings,
    cfg: TenantConfig,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    meeting_id: uuid.UUID,
    slot: Slot,
    prospect: Prospect,
    now: datetime,
    rescheduled: bool = False,
) -> None:
    token_id = conn.execute(
        text("INSERT INTO reschedule_tokens (tenant_id, meeting_id) VALUES (:t, :m) RETURNING id"),
        {"t": tenant_id, "m": meeting_id},
    ).scalar_one()
    signed = unsubscribe.sign(settings.key("unsubscribe_signing_key"), token_id, unsubscribe.RESCHEDULE)
    link = f"{settings.api_base_url}/r/{signed}"
    lg = cfg.language
    when = label(slot, prospect.tz, lg)
    lead = i18n.t(lg, "confirm_moved" if rescheduled else "confirm_booked")
    body = (
        f"{i18n.t(lg, 'greeting', name=prospect.first_name)}\n\n"
        f"{i18n.t(lg, 'confirm_body', lead=lead, when=when, minutes=cfg.calendar.meeting_minutes)}\n\n"
        f"{i18n.t(lg, 'agenda')}\n{cfg.show_rate.agenda}\n\n{i18n.t(lg, 'reschedule_link')}\n{link}\n\n"
        f"{cfg.brand_voice.sender_name.split()[0]}"
    )
    template_message(
        conn,
        cipher,
        settings,
        cfg,
        tenant_id=tenant_id,
        lead_id=lead_id,
        kind="meeting_confirmation",
        subject=i18n.t(lg, "confirm_subject", title=cfg.show_rate.meeting_title, when=when),
        body=body,
        status="approved",
        key=f"confirm:{meeting_id}:{token_id}",
        meeting_id=meeting_id,
    )
    conn.execute(
        text("UPDATE meeting_reminders SET status = 'cancelled' WHERE meeting_id = :m AND status = 'scheduled'"),
        {"m": meeting_id},
    )
    for off in cfg.show_rate.reminder_offsets_minutes:
        at = slot.starts_at - timedelta(minutes=off)
        if at > now:
            conn.execute(
                text("""INSERT INTO meeting_reminders (tenant_id, meeting_id, offset_minutes, scheduled_for,
                  idempotency_key) VALUES (:t, :m, :o, :at, :k)"""),
                {
                    "t": tenant_id,
                    "m": meeting_id,
                    "o": off,
                    "at": at,
                    "k": f"rem:{meeting_id}:{off}:{slot.starts_at.isoformat()}",
                },
            )
    conn.execute(
        text("UPDATE meetings SET brief_enc = :b WHERE id = :m"),
        {
            "b": cipher.encrypt(
                conn, tenant_id, "meetings.brief", build_brief(conn, cipher, tenant_id, lead_id, slot, cfg)
            ),
            "m": meeting_id,
        },
    )
    write_audit(
        conn,
        action="meeting.rescheduled" if rescheduled else "meeting.booked",
        actor_type="system",
        actor_id="booking",
        tenant_id=tenant_id,
        entity_type="meeting",
        entity_id=meeting_id,
    )


def build_brief(
    conn: Connection, cipher: PiiCipher, tenant_id: uuid.UUID, lead_id: uuid.UUID, slot: Slot, cfg: TenantConfig
) -> str:
    r = (
        conn.execute(
            text("""SELECT p.name_enc, p.title, p.country_code, c.name AS company, s.signal_summary, s.source_url,
          q.evidence FROM leads l JOIN prospects p ON p.id = l.prospect_id JOIN companies c ON c.id = l.company_id
          LEFT JOIN signals s ON s.id = l.signal_id LEFT JOIN qualifications q ON q.lead_id = l.id WHERE l.id = :l"""),
            {"l": lead_id},
        )
        .mappings()
        .one()
    )
    replies = conn.execute(
        text("SELECT body_text_enc, classification FROM replies WHERE lead_id = :l ORDER BY received_at"),
        {"l": lead_id},
    ).all()
    convo = "\n".join(
        f"- ({x.classification}) "
        + cipher.decrypt(conn, tenant_id, "replies.body", x.body_text_enc)[:300].replace("\n", " ")
        for x in replies
        if x.body_text_enc
    )
    ev = r["evidence"] or {}
    quals = "\n".join(
        f"- {k}: {'met' if v.get('met') else 'not shown'} ({v.get('evidence') or 'no evidence'})" for k, v in ev.items()
    )
    host = slot.starts_at.astimezone(ZoneInfo(cfg.calendar.host_timezone)).strftime("%A %d %B %H:%M")
    return (
        f"Pre-meeting brief\n\nWho: {cipher.decrypt(conn, tenant_id, 'prospects.name', r['name_enc'])}, {r['title']}, "
        f"{r['company'] or '[sole trader]'} ({r['country_code']})\nWhen: {host} ({cfg.calendar.host_timezone})\n\n"
        f"Why now (trigger): {r['signal_summary']}\nSource: {r['source_url']}\n\nConversation:\n{convo or '- none'}\n\n"
        f"Qualification evidence:\n{quals or '- not recorded'}\n\nAgenda:\n{cfg.show_rate.agenda}\n"
    )


def confirm(conn: Connection, tenant_id: uuid.UUID, meeting_id: uuid.UUID, response: str = "accepted") -> None:
    m = conn.execute(text("SELECT lead_id, status FROM meetings WHERE id = :m FOR UPDATE"), {"m": meeting_id}).one()
    conn.execute(
        text(
            "UPDATE meetings SET prospect_response = :r, status = CASE WHEN :r = 'accepted' AND status = 'booked' "
            "THEN 'confirmed' ELSE status END WHERE id = :m"
        ),
        {"r": response, "m": meeting_id},
    )
    if response == "accepted" and get_lead(conn, m.lead_id).state == State.BOOKED:
        transition(conn, m.lead_id, State.CONFIRMED, actor_type="system", actor_id="calendar", reason="invite accepted")


def reschedule(
    conn: Connection,
    cipher: PiiCipher,
    settings: Settings,
    cfg: TenantConfig,
    cal: CalendarGateway,
    *,
    tenant_id: uuid.UUID,
    meeting_id: uuid.UUID,
    new_slot: Slot,
    now: datetime,
) -> dict[str, object]:
    m = conn.execute(text("SELECT * FROM meetings WHERE id = :m FOR UPDATE"), {"m": meeting_id}).mappings().one()
    if m["status"] not in ("booked", "confirmed"):
        return {"status": "not_active"}
    if m["reschedule_count"] >= cfg.show_rate.max_reschedules:
        lead = get_lead(conn, m["lead_id"])
        conn.execute(
            text("""INSERT INTO escalations (tenant_id, lead_id, reason, context, pre_escalation_state)
              VALUES (:t, :l, 'reschedule_limit', CAST(:c AS jsonb), :p)"""),
            {
                "t": tenant_id,
                "l": m["lead_id"],
                "c": json.dumps({"meeting_id": str(meeting_id)}),
                "p": lead.state.value,
            },
        )
        transition(
            conn, m["lead_id"], State.ESCALATED, actor_type="system", actor_id="booking", reason="reschedule limit"
        )
        return {"status": "escalated"}
    busy = cal.busy(cfg.calendar.calendar_ref, new_slot.starts_at, new_slot.ends_at)
    if any(new_slot.overlaps(b) for b in busy):
        return {"status": "slot_taken"}
    cal.cancel_event(
        conn,
        tenant_id=tenant_id,
        meeting_id=meeting_id,
        calendar_ref=cfg.calendar.calendar_ref,
        provider_event_id=m["provider_event_id"] or "",
        idempotency_key=f"cancel:{meeting_id}:{m['reschedule_count']}",
    )
    try:
        event_id, _ = cal.create_event(
            conn,
            tenant_id=tenant_id,
            lead_id=m["lead_id"],
            meeting_id=meeting_id,
            calendar_ref=cfg.calendar.calendar_ref,
            slot=new_slot,
            title=cfg.show_rate.meeting_title,
            organiser=cfg.brand_voice.sender_name,
            description=cfg.show_rate.agenda,
            idempotency_key=f"evt:{meeting_id}:{m['reschedule_count'] + 1}",
        )
    except BookingBlocked as exc:
        return {"status": "blocked", "reasons": list(exc.reasons)}
    conn.execute(
        text("""UPDATE meetings SET starts_at = :s, ends_at = :e, provider_event_id = :ev, status = 'booked',
          prospect_response = NULL, reschedule_count = reschedule_count + 1 WHERE id = :m"""),
        {"s": new_slot.starts_at, "e": new_slot.ends_at, "ev": event_id, "m": meeting_id},
    )
    transition(conn, m["lead_id"], State.RESCHEDULED, actor_type="system", actor_id="booking", reason="rescheduled")
    transition(conn, m["lead_id"], State.BOOKED, actor_type="system", actor_id="booking", reason="new time booked")
    after_booking(
        conn,
        cipher,
        settings,
        cfg,
        tenant_id=tenant_id,
        lead_id=m["lead_id"],
        meeting_id=meeting_id,
        slot=new_slot,
        prospect=load_prospect(conn, cipher, tenant_id, m["lead_id"]),
        now=now,
        rescheduled=True,
    )
    return {"status": "rescheduled", "count": m["reschedule_count"] + 1}


def record_attendance(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    meeting_id: uuid.UUID,
    status: str,
    source: str,
    user_id: uuid.UUID | None,
    now: datetime,
    evidence: dict[str, str | None] | None = None,
) -> str:
    m = (
        conn.execute(text("SELECT lead_id, status, ends_at FROM meetings WHERE id = :m FOR UPDATE"), {"m": meeting_id})
        .mappings()
        .one()
    )
    if status in ("attended", "no_show") and m["ends_at"] > now:
        raise ValueError("attendance can only be recorded after the meeting has ended")
    conn.execute(
        text("""INSERT INTO attendance_events (tenant_id, meeting_id, source, status, recorded_by, evidence)
          VALUES (:t, :m, :s, :st, :u, CAST(:ev AS jsonb))"""),
        {"t": tenant_id, "m": meeting_id, "s": source, "st": status, "u": user_id, "ev": json.dumps(evidence or {})},
    )
    conn.execute(text("UPDATE meetings SET status = :st WHERE id = :m"), {"st": status, "m": meeting_id})
    conn.execute(
        text("UPDATE meeting_reminders SET status = 'cancelled' WHERE meeting_id = :m AND status = 'scheduled'"),
        {"m": meeting_id},
    )
    target = {"attended": State.ATTENDED, "no_show": State.NO_SHOW}.get(status)
    if target:
        transition(
            conn,
            m["lead_id"],
            target,
            actor_type="user" if user_id else "system",
            actor_id=str(user_id) if user_id else source,
            reason=f"attendance: {status} ({source})",
        )
    return status
