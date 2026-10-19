"""Draft-writing evaluation with no database: six synthetic leads in four languages go through the real writer prompt
(Agent 3), then the deterministic draft checks (sourced claims, no unsourced figures, formal address, no legal text,
no spam words, a question as the call to action...). One rewrite with the checks' findings, as in production.

Synthetic data only: fictional companies and people. Used by `pipelineos llm benchmark` to compare local models.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agents.injection import sanitise_untrusted
from app.agents.writer.agent import DRAFT, short_name
from app.agents.writer.checks import check_draft
from app.agents.writer.schemas import WriterDraft
from app.demo.dataset import DEMO_CONFIG
from app.llm.client import LLMBackend, LLMClient, LLMEscalation, UntrustedSource, wrap_untrusted
from app.llm.prompts import load_prompt


@dataclass(frozen=True)
class DraftCase:
    language: str
    first_name: str
    title: str
    company: str
    signal_type: str
    source: str


CASES = [
    DraftCase("en-GB", "Alex", "Head of IT Operations", "Lakeside Networks Ltd", "tender",
              "Harrow Council awarded the contract 'IT service desk support 2026-2028' to Lakeside Networks Ltd "
              "(value £240,000)."),
    DraftCase("en-GB", "Priya", "Service Delivery Director", "Contoso Managed IT Ltd", "hiring",
              "Contoso Managed IT Ltd published 3 new role(s) on its careers page: Service Desk Analyst (2nd line), "
              "Leeds; Service Desk Analyst, Remote; Team Lead, Service Desk, Leeds."),
    DraftCase("en-GB", "Sam", "Managing Director", "Fabrikam Cloud Ltd", "funding",
              "Fabrikam Cloud Ltd filed a statement of capital following an allotment of new shares on "
              "2 October 2026 (Companies House)."),
    DraftCase("fr-FR", "Claire", "Directrice des opérations", "Lumière Conseil SAS", "tender",
              "La ville de Lyon a attribué le marché « Support informatique des écoles 2026-2029 » à "
              "Lumière Conseil SAS."),
    DraftCase("es-ES", "Lucía", "Directora de TI", "Atlas Soluciones SL", "tender",
              "El Ayuntamiento de Valencia adjudicó el contrato de soporte informático de sus oficinas a "
              "Atlas Soluciones SL."),
    DraftCase("nl-BE", "Pieter", "Operationeel directeur", "Schelde Data NV", "tender",
              "De stad Gent gunde de opdracht 'IT-ondersteuning stadsdiensten 2026-2028' aan Schelde Data NV."),
]  # fmt: skip


@dataclass
class DraftEvalResult:
    cases: int = 0
    first_pass: int = 0
    pass_after_rewrite: int = 0
    escalated: int = 0
    seconds: float = 0.0
    failures: list[dict[str, Any]] = field(default_factory=list)

    def rate(self) -> float:
        return self.pass_after_rewrite / self.cases if self.cases else 0.0


def run(backend: LLMBackend, cases: list[DraftCase] | None = None) -> DraftEvalResult:
    llm = LLMClient(backend, {"volume": "local", "reasoning": "local"}, lambda _r: None, sleep=lambda _s: None)
    prompt, policy = load_prompt(DRAFT), load_prompt("common/untrusted_data_policy.v1.md").body
    cfg, tenant = DEMO_CONFIG, uuid.UUID(int=1)
    res = DraftEvalResult()
    t0 = time.monotonic()
    for i, case in enumerate(cases or CASES):
        res.cases += 1
        source = UntrustedSource("s1", sanitise_untrusted(case.source).text, "https://example.org/notice")
        data: dict[str, Any] = {
            "language": case.language,
            "follow_ups": cfg.sequence.follow_ups,
            "prospect": {"first_name": case.first_name, "title": case.title},
            "company": {"name": case.company, "short_name": short_name(case.company)},
            "signal": {"signal_type": case.signal_type, "detected_at": "2026-10-12T09:00:00+00:00"},
            "sender": {"first_name": cfg.brand_voice.sender_name.split()[0], "title": cfg.brand_voice.sender_title},
            "offer": {"value_proposition": cfg.brand_voice.value_proposition, "purpose": cfg.outreach_purpose},
            "tone": cfg.brand_voice.tone,
            "banned_phrases": cfg.brand_voice.banned_phrases,
        }
        wrapped = wrap_untrusted([source])
        try:
            for attempt in range(2):
                draft = llm.run_structured(
                    prompt=prompt,
                    schema=WriterDraft,
                    tenant_id=tenant,
                    lead_id=uuid.UUID(int=100 + i),
                    agent="writer",
                    policy=policy,
                    user_content=f"INPUT_JSON: {json.dumps(data)}\n{wrapped}",
                )
                check = check_draft(
                    draft,
                    source_ids={"s1"},
                    first_name=case.first_name,
                    language=case.language,
                    follow_ups_expected=cfg.sequence.follow_ups,
                    banned_phrases=cfg.brand_voice.banned_phrases,
                    source_texts={"s1": source.content},
                )
                if check.passed:
                    res.first_pass += attempt == 0
                    res.pass_after_rewrite += 1
                    break
                if attempt == 0:
                    data = {**data, "rewrite": {"previous_draft": draft.model_dump(), "issues": check.as_list()}}
                else:
                    res.failures.append({"case": case.company, "language": case.language, "issues": check.blocking})
        except LLMEscalation as exc:
            res.escalated += 1
            res.failures.append({"case": case.company, "language": case.language, "issues": [f"escalated: {exc}"]})
    res.seconds = time.monotonic() - t0
    return res
