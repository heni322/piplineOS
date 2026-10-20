"""Web research (Gemini + Google Search) behind the paid-call gate and a monthly cap.

- Only with the three keys (GEMINI_SEARCH_MODE=live, the chosen client live, global go-live with the checklist
  signed), never with FREE_ONLY=true. Otherwise the would-be question is written to the dry-run log and nothing is
  called. Every request is counted in `provider_usage` under GEMINI_SEARCH_MONTHLY_CAP first.
- The answer goes back to the operator who asked and is never stored (Google's terms for grounded results). Only the
  fact that a question was asked is audited, without its text or answer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.engine import Engine

from app.adapters.base import AdapterNotConfigured
from app.adapters.gemini_search import GeminiSearch, GroundedAnswer
from app.core.audit import write_audit
from app.core.crypto import PiiCipher
from app.db.session import DbContext, transaction
from app.domain.paid.gate import PaidGate
from app.domain.paid.places import CapReached, reserve_call
from app.settings import Settings

PROVIDER = "gemini.search"


@dataclass
class ResearchResult:
    called: bool
    reason: str | None = None  # why Google was not called: dry_run | cap_reached
    answer: GroundedAnswer | None = None


class GatedResearch:
    def __init__(self, provider: GeminiSearch, gate: PaidGate, engine: Engine, *, cap: int) -> None:
        self.provider, self.gate, self.engine, self.cap = provider, gate, engine, cap

    @property
    def allowed(self) -> bool:
        return self.gate.mode == "live"  # no sandbox: every grounded search is billed

    def ask(self, question: str, operator_id: uuid.UUID) -> ResearchResult:
        with transaction(DbContext.system(self.gate.tenant_id), self.engine) as conn:
            write_audit(
                conn,
                action="research.web_question",
                actor_type="user",
                actor_id=str(operator_id),
                tenant_id=self.gate.tenant_id,
                after={"called": self.allowed},  # never the question or the answer
            )
        if not self.allowed:
            self.gate.record(adapter=PROVIDER, operation="web_research", subject=question[:300], lead_id=None)
            return ResearchResult(called=False, reason="dry_run")
        try:
            reserve_call(self.engine, PROVIDER, self.cap)
        except CapReached:
            return ResearchResult(called=False, reason="cap_reached")
        return ResearchResult(called=True, answer=self.provider.ask(question))


def research_for(settings: Settings, engine: Engine, cipher: PiiCipher, tenant_id: uuid.UUID) -> GatedResearch:
    from app.domain.paid.providers import _gate

    if settings.free_only:
        raise AdapterNotConfigured("Web research with Gemini is a paid API: not available with FREE_ONLY=true")
    key = settings.gemini_api_key
    if key is None or not key.get_secret_value():
        raise AdapterNotConfigured("GEMINI_API_KEY is not set")
    gate = _gate(settings, engine, cipher, tenant_id, settings.gemini_search_mode)
    return GatedResearch(
        GeminiSearch(key.get_secret_value(), settings.gemini_model),
        gate,
        engine,
        cap=settings.gemini_search_monthly_cap,
    )
