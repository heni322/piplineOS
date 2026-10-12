from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReplyClass = Literal[
    "interested",
    "objection",
    "not_now",
    "referral",
    "out_of_office",
    "unsubscribe",
    "data_protection_request",
    "negative",
    "unclear",
]
SAFETY_CLASSES = frozenset({"unsubscribe", "objection", "data_protection_request"})


class Referral(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    email: str | None = None
    title: str | None = None


class ReplyClassification(BaseModel):
    """LLM output of reply_handler.classify."""

    model_config = ConfigDict(extra="forbid", title="ReplyClassification")
    classification: ReplyClass
    confidence: float = Field(ge=0, le=1)
    language: str = Field(min_length=2, max_length=7)
    evidence: list[str] = Field(default_factory=list, max_length=3)
    ooo_return_date: date | None = None
    referral: Referral | None = None
    sales_objection: str | None = Field(default=None, max_length=200)
    playbook_id: str | None = None
    slot_choice: int | None = Field(default=None, ge=1, le=5)
    wants_reschedule: bool = False
    asks_if_ai: bool = False
    escalate_flags: list[Literal["anger", "legal", "pricing", "contract", "complaint"]] = Field(default_factory=list)


class CriterionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    met: bool | None
    evidence_quote: str | None = Field(default=None, max_length=400)
    confidence: float = Field(ge=0, le=1)


class QualificationResult(BaseModel):
    """LLM output of reply_handler.qualify."""

    model_config = ConfigDict(extra="forbid", title="QualificationResult")
    criteria: list[CriterionResult]
    summary: str = Field(max_length=500)


class ReplyDraft(BaseModel):
    """LLM output of reply_handler.respond."""

    model_config = ConfigDict(extra="forbid", title="ReplyDraft")
    language: str
    body: str = Field(min_length=10, max_length=1500)
    playbook_id: str | None = None


class HandledReply(BaseModel):
    """Agent 4 combined decision (rules + model), stored as the reply's classification + rule flags."""

    model_config = ConfigDict(extra="forbid")
    classification: ReplyClass
    confidence: float
    language: str
    source: Literal["rules", "model", "rules+model"]
    escalate_reasons: list[str] = []
    llm: ReplyClassification | None = None
