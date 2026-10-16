"""Agent 4 — Reply Handler & Qualifier. Produces validated data only; app.domain.replies decides the actions."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date

from app.agents.base import AgentContext
from app.agents.injection import sanitise_untrusted
from app.agents.reply_handler.rules import RuleResult
from app.agents.reply_handler.schemas import (
    SAFETY_CLASSES,
    HandledReply,
    QualificationResult,
    ReplyClassification,
    ReplyDraft,
)
from app.agents.writer.checks import EMAIL, MASK_TOKEN, PHONE, SPAM, URL, _uk_spelling_issues, words
from app.domain.tenants.config import PlaybookEntry, TenantConfig
from app.llm.client import LLMEscalation, UntrustedSource, wrap_untrusted
from app.llm.prompts import load_prompt

SAFETY_PRECEDENCE = ["data_protection_request", "objection", "unsubscribe"]
MAX_REPLY_WORDS = 90


def _policy() -> str:
    return load_prompt("common/untrusted_data_policy.v1.md").body


def classify(
    ctx: AgentContext, lead_id: uuid.UUID, text: str, rules: RuleResult, *, offered_slots: list[str], received: date
) -> HandledReply:
    """Rules first; the model adds nuance. Safety outcomes can only be ADDED by the model, never removed."""
    clean = sanitise_untrusted(text)
    escalate: list[str] = list(rules.flags)
    if clean.injection_suspected:
        escalate.append("prompt_injection")
    data = {
        "playbook": [{"id": p.id, "objection": p.objection} for p in ctx.config.objection_playbook],
        "offered_slots": offered_slots,
        "received_on": received.isoformat(),
    }
    llm: ReplyClassification | None = None
    try:
        llm = ctx.llm.run_structured(
            prompt=load_prompt("reply_handler/classify.v1.md"),
            schema=ReplyClassification,
            tenant_id=ctx.tenant_id,
            lead_id=lead_id,
            agent="reply_handler.classify",
            policy=_policy(),
            user_content=f"INPUT_JSON: {json.dumps(data)}\n" + wrap_untrusted([UntrustedSource("reply", clean.text)]),
        )
    except LLMEscalation as exc:
        escalate.append(f"llm_{exc.reason}")

    safety_votes = [c for c in (rules.safety, llm.classification if llm else None) if c in SAFETY_CLASSES]
    if safety_votes:
        cls = min(safety_votes, key=SAFETY_PRECEDENCE.index)
        src = (
            "rules+model"
            if rules.safety and llm and llm.classification in SAFETY_CLASSES
            else ("rules" if rules.safety else "model")
        )
        return HandledReply(
            classification=cls,
            confidence=1.0 if rules.safety else (llm.confidence if llm else 0.0),
            language=rules.language,
            source=src,
            escalate_reasons=escalate,
            llm=llm,
        )
    if rules.out_of_office and (llm is None or llm.classification in ("out_of_office", "unclear")):
        return HandledReply(
            classification="out_of_office",
            confidence=1.0,
            language=rules.language,
            source="rules",
            escalate_reasons=[e for e in escalate if e != "prompt_injection"],
            llm=llm,
        )
    if llm is None:
        return HandledReply(
            classification="unclear",
            confidence=0.0,
            language=rules.language,
            source="rules",
            escalate_reasons=[*escalate, "llm_failure"],
        )
    escalate += [f for f in llm.escalate_flags if f not in escalate]
    if llm.confidence < 0.70:
        escalate.append("low_confidence")
    if llm.classification == "unclear":
        escalate.append("unclear")
    lang = llm.language if rules.language == "unknown" else rules.language
    base = lang.split("-")[0].lower()
    supported = {x.split("-")[0].lower() for x in ctx.config.supported_languages}
    if base not in supported and base != "unknown" and llm.classification != "out_of_office":
        escalate.insert(0, "unsupported_language")  # never answer in a language the tenant has not approved
    if llm.sales_objection and not llm.playbook_id:
        escalate.append("off_playbook")
    if llm.playbook_id and llm.playbook_id not in {p.id for p in ctx.config.objection_playbook}:
        escalate.append("off_playbook")
    return HandledReply(
        classification=llm.classification,
        confidence=llm.confidence,
        language=lang,
        source="model",
        escalate_reasons=escalate,
        llm=llm,
    )


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def qualify(
    ctx: AgentContext, lead_id: uuid.UUID, reply_texts: list[str], role_relevance: float | None, title: str | None
) -> tuple[bool | None, dict[str, dict[str, object]], float]:
    """Returns (overall_met: True/False/None=incomplete, evidence per criterion, min confidence).
    Evidence for 'reply' criteria must be a verbatim quote from what the prospect wrote; otherwise it is ignored."""
    q = ctx.config.qualification
    if q is None:
        return None, {}, 0.0
    joined = "\n---\n".join(sanitise_untrusted(t).text for t in reply_texts)
    data = {"criteria": [c.model_dump(include={"id", "question", "evidence_from"}) for c in q.criteria]}
    res = ctx.llm.run_structured(  # LLMEscalation propagates: the caller escalates (never "not met" by default)
        prompt=load_prompt("reply_handler/qualify.v1.md"),
        schema=QualificationResult,
        tenant_id=ctx.tenant_id,
        lead_id=lead_id,
        agent="reply_handler.qualify",
        policy=_policy(),
        user_content=f"INPUT_JSON: {json.dumps(data)}\n" + wrap_untrusted([UntrustedSource("reply", joined)]),
    )
    by_id = {c.id: c for c in res.criteria}
    evidence: dict[str, dict[str, object]] = {}
    confidences: list[float] = []
    for c in q.criteria:
        met: bool | None
        if c.evidence_from == "profile":
            met = role_relevance is not None and role_relevance >= 0.6
            evidence[c.id] = {"met": met, "evidence": f"job title: {title}", "source": "profile", "confidence": 0.9}
            confidences.append(0.9)
            continue
        r = by_id.get(c.id)
        quote_ok = bool(r and r.evidence_quote and _norm(r.evidence_quote) in _norm(joined))
        met = r.met if (r and quote_ok) else None
        evidence[c.id] = {
            "met": met,
            "evidence": r.evidence_quote if quote_ok and r else None,
            "source": "reply",
            "confidence": r.confidence if r else 0.0,
            "rejected_quote": bool(r and r.evidence_quote and not quote_ok),
        }
        if met is not None and r:
            confidences.append(r.confidence)
    required = [evidence[c.id]["met"] for c in q.criteria if c.required]
    if any(m is False for m in required):
        overall: bool | None = False
    elif all(m is True for m in required):
        overall = True
    else:
        overall = None
    conf = min(confidences) if confidences else 0.0
    if overall is True and conf < q.min_confidence:
        overall = None
    return overall, evidence, conf


def check_reply(body: str, cfg: TenantConfig, language: str, need_disclosure: bool) -> list[str]:
    issues = []
    if words(body) > MAX_REPLY_WORDS + (len(cfg.ai_disclosure_text.split()) if need_disclosure else 0):
        issues.append("reply too long")
    if URL.search(body) or EMAIL.search(body) or PHONE.search(body):
        issues.append("links, email addresses or phone numbers are not allowed")
    if MASK_TOKEN.search(body):
        issues.append("contains masked tokens")
    if re.search(r"unsubscribe|registered in|company number", body, re.IGNORECASE):
        issues.append("footer text must not be written by the model")
    if re.search(r"[£$€]\s?\d|\bper (?:hour|month|user)\b|\bguarantee", body, re.IGNORECASE):
        issues.append("prices or guarantees are not allowed in AI replies")
    low = body.lower()
    issues += [f"spam trigger {w!r}" for w in SPAM if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low)]
    issues += [f"banned phrase {p!r}" for p in cfg.brand_voice.banned_phrases if p.lower() in low]
    if re.search(r"\b(i am|i'm) (a )?(human|real person)\b", low):
        issues.append("must never claim to be human")
    if language.startswith("en"):
        issues += _uk_spelling_issues(body)
    return issues


def draft_reply(
    ctx: AgentContext,
    lead_id: uuid.UUID,
    *,
    intent: str,
    first_name: str,
    reply_text: str,
    language: str,
    playbook: PlaybookEntry | None = None,
    missing_criteria: list[str] | None = None,
) -> str | None:
    """Drafts a playbook-bound reply; returns None if it fails the gate twice (caller escalates)."""
    cfg = ctx.config
    data = {
        "intent": intent,
        "language": cfg.language if language == "en" else language,
        "prospect_first_name": first_name,
        "sender_first_name": cfg.brand_voice.sender_name.split()[0],
        "playbook_entry": playbook.model_dump() if playbook else None,
        "missing_criteria": missing_criteria or [],
        "banned_phrases": cfg.brand_voice.banned_phrases,
    }
    wrapped = wrap_untrusted([UntrustedSource("reply", sanitise_untrusted(reply_text).text)])
    issues: list[str] = []
    for _ in range(2):
        payload = {**data, "fix_issues": issues} if issues else data
        try:
            d = ctx.llm.run_structured(
                prompt=load_prompt("reply_handler/respond.v1.md"),
                schema=ReplyDraft,
                tenant_id=ctx.tenant_id,
                lead_id=lead_id,
                agent="reply_handler.respond",
                policy=_policy(),
                user_content=f"INPUT_JSON: {json.dumps(payload)}\n{wrapped}",
            )
        except LLMEscalation:  # no draft: every caller already escalates to a human on None
            return None
        if intent == "playbook" and playbook and d.playbook_id != playbook.id:
            issues = [f"must use playbook entry {playbook.id}"]
            continue
        issues = check_reply(d.body, cfg, language, need_disclosure=False)
        if not issues:
            return d.body
    return None
