"""Alternative-channel tasks: the human work queue for leads that must not (or cannot) be emailed automatically.

In free-only mode most of these are "published_contact" tasks: a decision-maker found on the public register with
no email address, to be reached through the company's own published channels. Personal data stays masked here;
the operator unmasks a name through the logged lead unmask when they act on a task.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import CurrentUser, cipher_dep, require
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.core.errors import ProblemError
from app.core.masking import mask_name
from app.core.security import Permission
from app.db.session import get_engine, transaction

router = APIRouter(tags=["tasks"])
Operator = Annotated[CurrentUser, Depends(require(Permission.MANAGE_COMPLIANCE))]
Cipher = Annotated[PiiCipher, Depends(cipher_dep)]


class TaskOut(BaseModel):
    id: str
    lead_id: str
    tenant_id: str
    company: str
    company_number: str | None
    company_domain: str | None
    prospect: str | None  # masked
    prospect_title: str | None
    signal: str | None
    signal_url: str | None
    channel: str
    instructions: str
    status: str
    outcome_note: str | None
    created_at: datetime


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    user: Operator,
    cipher: Cipher,
    tenant_id: uuid.UUID | None = None,
    status: Literal["open", "done", "skipped", "all"] = "open",
) -> list[TaskOut]:
    with transaction(user.db_context(tenant_id), get_engine()) as conn:
        rows = (
            conn.execute(
                text("""SELECT t.id, t.lead_id, t.tenant_id, t.channel, t.instructions, t.status, t.outcome_note,
                  t.created_at,
                  c.name AS company, c.company_number, c.domain_normalised, p.name_enc, p.title,
                  s.signal_summary, s.source_url
                FROM alternative_channel_tasks t JOIN leads l ON l.id = t.lead_id
                JOIN companies c ON c.id = l.company_id LEFT JOIN prospects p ON p.id = l.prospect_id
                LEFT JOIN signals s ON s.id = l.signal_id
                WHERE (:s = 'all' OR t.status = :s) ORDER BY t.created_at DESC LIMIT 200"""),
                {"s": status},
            )
            .mappings()
            .all()
        )
        out = []
        for r in rows:
            name = cipher.decrypt(conn, r["tenant_id"], "prospects.name", r["name_enc"]) if r["name_enc"] else None
            out.append(
                TaskOut(
                    id=str(r["id"]),
                    lead_id=str(r["lead_id"]),
                    tenant_id=str(r["tenant_id"]),
                    company=r["company"] or "[personal name]",
                    company_number=r["company_number"],
                    company_domain=r["domain_normalised"],
                    prospect=mask_name(name) if name else None,
                    prospect_title=r["title"],
                    signal=r["signal_summary"],
                    signal_url=r["source_url"],
                    channel=r["channel"],
                    instructions=r["instructions"],
                    status=r["status"],
                    outcome_note=r["outcome_note"],
                    created_at=r["created_at"],
                )
            )
    return out


class CompleteIn(BaseModel):
    outcome: Literal["done", "skipped"]
    note: str = Field(min_length=3, max_length=1000)


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: uuid.UUID, body: CompleteIn, user: Operator) -> dict[str, str]:
    with transaction(user.db_context(), get_engine()) as conn:
        t = (
            conn.execute(
                text("SELECT id, tenant_id, lead_id, status FROM alternative_channel_tasks WHERE id = :t FOR UPDATE"),
                {"t": task_id},
            )
            .mappings()
            .first()
        )
        if t is None:
            raise ProblemError(404, "Task not found")
        if t["status"] != "open":
            raise ProblemError(409, "Task already closed")
        conn.execute(
            text("""UPDATE alternative_channel_tasks SET status = :s, assigned_to = :u, outcome_note = :n,
              closed_at = now() WHERE id = :t"""),
            {"s": body.outcome, "u": user.id, "n": body.note, "t": task_id},
        )
        write_audit(
            conn,
            action=f"alternative_task.{body.outcome}",
            actor_type="user",
            actor_id=str(user.id),
            tenant_id=t["tenant_id"],
            entity_type="alternative_channel_task",
            entity_id=task_id,
            after={"outcome": body.outcome},  # the note stays on the task (RLS), never in the append-only log
        )
    return {"status": body.outcome}
