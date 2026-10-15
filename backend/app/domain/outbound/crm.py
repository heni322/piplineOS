"""The client's CRM: each meeting PipelineOS books is written to the client's own CRM (company, contact, a deal whose
stage follows the meeting), and an "attended" stage the client sets there counts as attendance evidence.

Writing to a CRM is a side effect, so it follows the brief's rule exactly like sending email: it happens only when
the CRM adapter mode is live, the client is in live mode AND global go-live is on with the checklist signed.
Otherwise the full would-be write is recorded (encrypted) in the dry-run log. "sandbox" mode is treated as dry-run
here: the connected account is the client's REAL CRM, so there is no safe test target.

Personal data is only sent while the person may still be processed: if they objected, were suppressed or erased,
only an existing deal's stage may change. The sync records hold only the CRM's object ids, never personal data.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.adapters.base import RateLimited
from app.adapters.crm.base import CrmAdapter, CrmContact, CrmIds, CrmMeetingRecord
from app.adapters.crm.hubspot import HubSpotCrm
from app.adapters.http import ProviderError
from app.core.audit import write_audit
from app.core.crypto import PiiCipher, blind_index
from app.core.logging import get_logger
from app.db.session import DbContext, transaction
from app.domain.outbound.modes import resolve_mode
from app.domain.suppression.service import RecipientHashes
from app.domain.suppression.service import check as check_suppression
from app.domain.tenants.config import load_config
from app.settings import Settings

log = get_logger(__name__)
STATUSES = ("booked", "confirmed", "attended", "no_show", "cancelled")
PROVIDERS = ("hubspot",)
MAX_ATTEMPTS = 5
BATCH = 200
_TOKEN = re.compile(r"^[A-Za-z0-9_\-.:]{10,500}$")
_STAGE = re.compile(r"^[A-Za-z0-9_\-.:]{1,100}$")


class CrmConfigError(ValueError):
    pass


def meeting_ref(meeting_id: uuid.UUID) -> str:
    """Short reference written into the deal name: one token, so CRM search finds the deal again on a retry."""
    return f"PO{meeting_id.hex[:10].upper()}"


# --------------------------------------------------------------------------------------------- connection
def connection_status(conn: Connection, tenant_id: uuid.UUID) -> dict[str, Any] | None:
    """Everything about the connection except the token, which is never read back out."""
    row = (
        conn.execute(
            text("""SELECT provider, pipeline_id, stage_map, status, created_at, updated_at, last_sync_at,
                      disconnected_reason FROM crm_connections WHERE tenant_id = :t"""),
            {"t": tenant_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    counts: dict[str, int] = dict(
        conn.execute(
            text("SELECT state, count(*) FROM crm_links WHERE tenant_id = :t GROUP BY state"), {"t": tenant_id}
        )
        .tuples()
        .all()
    )
    return {**dict(row), "links": {k: int(v) for k, v in counts.items()}}


def connect(
    conn: Connection,
    cipher: PiiCipher,
    *,
    tenant_id: uuid.UUID,
    provider: str,
    token: str,
    pipeline_id: str,
    stage_map: dict[str, str],
    user_id: uuid.UUID,
    actor_type: str = "user",
) -> None:
    if provider not in PROVIDERS:
        raise CrmConfigError("Only HubSpot is supported for now")
    token = token.strip()
    if not _TOKEN.match(token):
        raise CrmConfigError("That doesn't look like a HubSpot access token")
    if not _STAGE.match(pipeline_id.strip()):
        raise CrmConfigError("Pipeline id: letters, numbers, - _ . : only")
    if set(stage_map) != set(STATUSES) or not all(_STAGE.match(v.strip()) for v in stage_map.values()):
        raise CrmConfigError(f"A stage id is needed for each of: {', '.join(STATUSES)}")
    stages = {k: v.strip() for k, v in stage_map.items()}
    conn.execute(
        text("""INSERT INTO crm_connections (tenant_id, provider, token_enc, pipeline_id, stage_map, connected_by)
                VALUES (:t, :p, :tok, :pl, CAST(:sm AS jsonb), :u)
                ON CONFLICT (tenant_id) DO UPDATE SET provider = :p, token_enc = :tok, pipeline_id = :pl,
                  stage_map = CAST(:sm AS jsonb), status = 'connected', connected_by = :u,
                  disconnected_reason = NULL"""),
        {
            "t": tenant_id,
            "p": provider,
            "tok": cipher.encrypt(conn, tenant_id, "crm_connections.token", token),
            "pl": pipeline_id.strip(),
            "sm": json.dumps(stages),
            "u": user_id,
        },
    )
    write_audit(
        conn,
        action="crm.connected",
        actor_type=actor_type,  # type: ignore[arg-type]
        actor_id=str(user_id),
        tenant_id=tenant_id,
        entity_type="crm_connection",
        entity_id=tenant_id,
        after={"provider": provider, "pipeline_id": pipeline_id.strip(), "stage_map": stages},  # never the token
    )


def disconnect(conn: Connection, *, tenant_id: uuid.UUID, user_id: uuid.UUID, reason: str) -> bool:
    n = conn.execute(
        text("""UPDATE crm_connections SET status = 'disconnected', disconnected_reason = :r
                WHERE tenant_id = :t AND status = 'connected'"""),
        {"t": tenant_id, "r": reason.strip()[:500]},
    ).rowcount
    if n:
        write_audit(
            conn,
            action="crm.disconnected",
            actor_type="user",
            actor_id=str(user_id),
            tenant_id=tenant_id,
            entity_type="crm_connection",
            entity_id=tenant_id,
            after={"reason": reason.strip()[:500]},
        )
    return bool(n)


# ------------------------------------------------------------------------------------------------- sync
@dataclass
class CrmSyncResult:
    mode: str = "none"
    recorded: int = 0  # dry-run records
    written: int = 0  # live writes
    skipped: int = 0  # nothing may be sent (no personal data allowed and no deal yet)
    errors: int = 0
    attendance: int = 0
    rate_limited: bool = False


def _error_label(exc: Exception) -> str:
    """Stored on the sync record: never the provider's message, which may echo personal data."""
    if isinstance(exc, ProviderError):
        return f"provider error {exc.status}"
    return type(exc).__name__


def sync_tenant(
    engine: Engine,
    settings: Settings,
    cipher: PiiCipher,
    tenant_id: uuid.UUID,
    *,
    now: datetime,
    live_adapter: CrmAdapter | None = None,
) -> CrmSyncResult:
    res = CrmSyncResult()
    with transaction(DbContext.system(tenant_id), engine) as conn:
        c = (
            conn.execute(
                text("SELECT * FROM crm_connections WHERE tenant_id = :t AND status = 'connected'"), {"t": tenant_id}
            )
            .mappings()
            .first()
        )
        if c is None:
            return res
        live = bool(conn.execute(text("SELECT live_mode FROM tenants WHERE id = :t"), {"t": tenant_id}).scalar())
        golive = bool(conn.execute(text("SELECT golive_complete('uk')")).scalar())
        _, cfg = load_config(conn, tenant_id)
        token = cipher.decrypt(conn, tenant_id, "crm_connections.token", c["token_enc"])
    mode = resolve_mode(
        adapter_mode=settings.crm_adapter_mode,
        tenant_live=live,
        global_go_live=settings.global_go_live,
        golive_complete=golive,
    )
    mode = "live" if mode == "live" else "dry_run"  # sandbox would still write to the client's real CRM
    res.mode = mode
    adapter: CrmAdapter | None = None
    if mode == "live":
        adapter = live_adapter or HubSpotCrm(token)
        res.attendance = _read_attendance(engine, tenant_id, adapter, c["stage_map"], now)

    with transaction(DbContext.system(tenant_id), engine) as conn:
        rows = (
            conn.execute(
                text("""SELECT m.id, m.lead_id, m.status, m.starts_at, m.ends_at, m.updated_at, k.id AS link_id,
                          k.company_ext_id, k.contact_ext_id, k.deal_ext_id, k.state
                        FROM meetings m LEFT JOIN crm_links k ON k.meeting_id = m.id
                        WHERE m.booked_by_system AND m.status = ANY(:st)
                          AND NOT coalesce(k.state = 'error' AND k.attempts >= :max, false)
                          AND (k.id IS NULL OR k.synced_at IS NULL OR m.updated_at > k.synced_at
                               OR k.synced_mode IS DISTINCT FROM :mode OR k.state IN ('creating', 'error'))
                        ORDER BY m.starts_at LIMIT :n"""),
                {"st": list(STATUSES), "mode": mode, "max": MAX_ATTEMPTS, "n": BATCH},
            )
            .mappings()
            .all()
        )
    for r in rows:
        try:
            outcome = _sync_one(
                engine, settings, cipher, tenant_id, c, cfg.show_rate.meeting_title, r, mode, adapter, now
            )
        except RateLimited:
            res.rate_limited = True
            break  # the next run continues; the record stays as it was
        if outcome == "recorded":
            res.recorded += 1
        elif outcome == "written":
            res.written += 1
        elif outcome == "skipped":
            res.skipped += 1
        else:
            res.errors += 1
    with transaction(DbContext.system(tenant_id), engine) as conn:
        conn.execute(
            text("UPDATE crm_connections SET last_sync_at = :n WHERE tenant_id = :t"), {"n": now, "t": tenant_id}
        )
    if rows or res.attendance:
        log.info(
            "crm_sync",
            tenant_id=str(tenant_id),
            mode=mode,
            recorded=res.recorded,
            written=res.written,
            skipped=res.skipped,
            errors=res.errors,
            attendance=res.attendance,
        )
    return res


def _record(
    conn: Connection, cipher: PiiCipher, tenant_id: uuid.UUID, meeting: Any, mode: str, outcome: dict[str, Any]
) -> None:
    conn.execute(
        text("""INSERT INTO crm_links (tenant_id, meeting_id, provider) VALUES (:t, :m, 'hubspot')
                ON CONFLICT (meeting_id) DO NOTHING"""),
        {"t": tenant_id, "m": meeting["id"]},
    )
    conn.execute(
        text("""UPDATE crm_links SET company_ext_id = coalesce(:co, company_ext_id),
                  contact_ext_id = coalesce(:ct, contact_ext_id), deal_ext_id = coalesce(:d, deal_ext_id),
                  synced_status = :st, synced_mode = :mode, state = :state, synced_at = :at,
                  attempts = CASE WHEN :state = 'error' THEN attempts + 1
                                  WHEN :state = 'creating' THEN attempts ELSE 0 END,
                  last_error = :err
                WHERE meeting_id = :m"""),
        {
            "m": meeting["id"],
            "co": outcome.get("company_id"),
            "ct": outcome.get("contact_id"),
            "d": outcome.get("deal_id"),
            "st": meeting["status"],
            "mode": mode,
            "state": outcome["state"],
            # the meeting's own last-change stamp (same clock as meetings.updated_at): a later change makes it due
            # again. A failed or unfinished attempt keeps no stamp, so the meeting stays due.
            "at": None if outcome["state"] in ("error", "creating") else meeting["updated_at"],
            "err": outcome.get("error"),
        },
    )


def _sync_one(
    engine: Engine,
    settings: Settings,
    cipher: PiiCipher,
    tenant_id: uuid.UUID,
    c: Any,
    title: str,
    m: Any,
    mode: str,
    adapter: CrmAdapter | None,
    now: datetime,
) -> str:
    with transaction(DbContext.system(tenant_id), engine) as conn:
        p = (
            conn.execute(
                text("""SELECT co.name, co.name_is_personal, co.domain_normalised, pr.name_enc, pr.email_enc,
                          pr.title, l.state FROM leads l JOIN companies co ON co.id = l.company_id
                          LEFT JOIN prospects pr ON pr.id = l.prospect_id WHERE l.id = :l"""),
                {"l": m["lead_id"]},
            )
            .mappings()
            .one()
        )
        contact: CrmContact | None = None
        email: str | None = None
        if p["email_enc"] is not None and p["name_enc"] is not None:
            email = cipher.decrypt(conn, tenant_id, "prospects.email", p["email_enc"])
            sup = check_suppression(
                conn, tenant_id, RecipientHashes.for_email(settings.key("suppression_hash_key"), email)
            )
            if not (sup.global_hit or sup.tenant_hit):
                full = cipher.decrypt(conn, tenant_id, "prospects.name", p["name_enc"]).split()
                contact = CrmContact(email, full[0], " ".join(full[1:]) or "-", p["title"])
        company = None if p["name_is_personal"] else p["name"]
        ref = meeting_ref(m["id"])
        record = CrmMeetingRecord(
            ref=ref,
            deal_name=f"{title} - {company or p['domain_normalised'] or 'prospect'} [{ref}]",
            stage_id=str(c["stage_map"][m["status"]]),
            pipeline_id=str(c["pipeline_id"]),
            starts_at=m["starts_at"],
            company_name=company,
            company_domain=p["domain_normalised"],
            contact=contact,
        )
        if mode == "dry_run":
            payload = {
                "to": contact.email if contact else None,
                "subject": f"HubSpot: {record.deal_name} -> {m['status']}",
                "record": {
                    "deal_name": record.deal_name,
                    "pipeline_id": record.pipeline_id,
                    "stage_id": record.stage_id,
                    "starts_at": record.starts_at.isoformat(),
                    "company": record.company_name,
                    "domain": record.company_domain,
                    "contact": contact.__dict__ if contact else None,
                },
            }
            conn.execute(
                text("""INSERT INTO dry_run_log (tenant_id, effect_type, adapter, idempotency_key, entity_id,
                          payload_enc, recipient_bidx) VALUES (:t, 'crm_write', 'crm.hubspot', :k, :e, :p, :b)
                        ON CONFLICT (tenant_id, effect_type, idempotency_key) DO NOTHING"""),
                {
                    "t": tenant_id,
                    "k": f"crm:{m['id']}:{m['status']}:{m['starts_at'].isoformat()}:{'c' if contact else 'n'}",
                    "e": m["id"],
                    "p": cipher.encrypt(conn, tenant_id, "dry_run_log.payload", json.dumps(payload)),
                    "b": blind_index(settings.key("blind_index_key"), contact.email, tenant_id) if contact else None,
                },
            )
            _record(conn, cipher, tenant_id, m, mode, {"state": "ok" if contact else "skipped"})
            return "recorded" if contact else "skipped"
        if contact is None and m["deal_ext_id"] is None:
            _record(conn, cipher, tenant_id, m, mode, {"state": "skipped"})
            return "skipped"
        if m["status"] in ("booked", "confirmed") and m["ends_at"] < now and m["deal_ext_id"] is not None:
            # the meeting is over: the client may be moving the deal on in their CRM; we only read it now
            _record(conn, cipher, tenant_id, m, mode, {"state": "ok"})
            return "skipped"
        # mark the attempt BEFORE calling out: a crash then leaves "creating", and the retry finds the deal by ref
        _record(conn, cipher, tenant_id, m, mode, {"state": "creating"})
    if adapter is None:
        raise RuntimeError("no live CRM adapter")
    known = CrmIds(m["company_ext_id"], m["contact_ext_id"], m["deal_ext_id"])
    try:
        ids = adapter.sync(record, known)
    except RateLimited:
        raise
    except Exception as exc:  # every failure is recorded and retried a bounded number of times
        with transaction(DbContext.system(tenant_id), engine) as conn:
            _record(conn, cipher, tenant_id, m, mode, {"state": "error", "error": _error_label(exc)})
        log.warning("crm_sync_failed", tenant_id=str(tenant_id), meeting_id=str(m["id"]), error=_error_label(exc))
        return "error"
    with transaction(DbContext.system(tenant_id), engine) as conn:
        _record(
            conn,
            cipher,
            tenant_id,
            m,
            mode,
            {
                "state": "ok",
                "company_id": ids.company_id,
                "contact_id": ids.contact_id,
                "deal_id": ids.deal_id,
            },
        )
        write_audit(
            conn,
            action="crm.synced",
            actor_type="system",
            actor_id="crm_sync",
            tenant_id=tenant_id,
            entity_type="meeting",
            entity_id=m["id"],
            after={"status": m["status"], "created": ids.created, "personal_data_sent": contact is not None},
        )
    return "written"


def _read_attendance(
    engine: Engine, tenant_id: uuid.UUID, adapter: CrmAdapter, stage_map: dict[str, str], now: datetime
) -> int:
    """Meetings that have ended: if the client moved the deal to their "attended" (or "no-show") stage, that is
    attendance evidence from the CRM (billing accepts it; our own operators' word alone is not enough)."""
    from app.domain.meetings.service import record_attendance

    with transaction(DbContext.system(tenant_id), engine) as conn:
        due = conn.execute(
            text("""SELECT k.meeting_id, k.deal_ext_id FROM crm_links k JOIN meetings m ON m.id = k.meeting_id
                    WHERE k.deal_ext_id IS NOT NULL AND k.synced_mode = 'live'
                      AND m.status IN ('booked','confirmed') AND m.ends_at < :n LIMIT :b"""),
            {"n": now, "b": BATCH},
        ).all()
    found = 0
    for meeting_id, deal_id in due:
        stage = adapter.deal_stage(deal_id)
        status = next((s for s in ("attended", "no_show") if stage is not None and stage == stage_map.get(s)), None)
        if status is None:
            continue
        with transaction(DbContext.system(tenant_id), engine) as conn:
            record_attendance(
                conn,
                tenant_id=tenant_id,
                meeting_id=meeting_id,
                status=status,
                source="crm",
                user_id=None,
                now=now,
                evidence={"crm": "hubspot", "deal_id": deal_id, "stage": stage},
            )
        found += 1
    return found
