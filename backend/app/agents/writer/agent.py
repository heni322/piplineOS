"""Agent 3 — Personalization Writer: source-grounded opener + follow-ups, deterministic compliance footer,
self-critique (rewrite if < 8/10, max 2 rewrites, then a human), every claim stored with its source URL."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import RowMapping, text

from app.agents.base import AgentContext
from app.agents.injection import sanitise_untrusted
from app.agents.writer.checks import check_draft
from app.agents.writer.schemas import PersonalizationSource, WriterCritique, WriterDraft, WriterOutput
from app.config_paths import config_root
from app.core.audit import write_audit
from app.db.session import DbContext, transaction
from app.domain.leads.state_machine import State, transition
from app.domain.outbound.footer import FooterError, FooterTemplate
from app.domain.outbound.messages import create_draft
from app.llm.client import LLMEscalation, UntrustedSource, wrap_untrusted
from app.llm.prompts import load_prompt

DRAFT, CRITIQUE = "writer/draft.v2.md", "writer/critique.v1.md"
LEGAL_SUFFIX = re.compile(r"\s+(ltd|limited|plc|llp|lp|inc|gmbh|sarl|sas|bv)\.?$", re.IGNORECASE)


@dataclass
class WriteResult:
    status: str  # pending_approval | escalated | skipped
    output: WriterOutput | None = None
    message_id: uuid.UUID | None = None
    reason: str = ""


def short_name(company: str) -> str:
    return LEGAL_SUFFIX.sub("", company).strip()


def write_for_lead(ctx: AgentContext, lead_id: uuid.UUID) -> WriteResult:
    cfg = ctx.config
    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        r = (
            conn.execute(
                text("""
            SELECT l.state, l.campaign_id, p.name_enc, p.email_enc, p.title, c.name AS company, c.name_enc AS cname_enc,
                   s.id AS signal_id, s.signal_type, s.signal_summary, s.source_url, s.detected_at,
                   t.legal_name, t.company_number, t.registered_address, t.slug
            FROM leads l JOIN prospects p ON p.id = l.prospect_id JOIN companies c ON c.id = l.company_id
            JOIN tenants t ON t.id = l.tenant_id LEFT JOIN signals s ON s.id = l.signal_id WHERE l.id = :l"""),
                {"l": lead_id},
            )
            .mappings()
            .one()
        )
        if r["state"] != State.COMPLIANCE_CHECKED.value:
            return WriteResult("skipped", reason=f"lead in state {r['state']}")
        name = ctx.cipher.decrypt(conn, ctx.tenant_id, "prospects.name", r["name_enc"])
        email = ctx.cipher.decrypt(conn, ctx.tenant_id, "prospects.email", r["email_enc"])
        company = r["company"] or ctx.cipher.decrypt(conn, ctx.tenant_id, "companies.name", r["cname_enc"])
        inbox_id = conn.execute(
            text("""SELECT i.id FROM inboxes i WHERE i.status IN ('active','warming')
              ORDER BY (SELECT count(*) FROM messages m WHERE m.inbox_id = i.id), i.created_at LIMIT 1""")
        ).scalar()

    sources = []
    if r["signal_summary"]:
        clean = sanitise_untrusted(r["signal_summary"])
        sources.append(UntrustedSource("s1", clean.text, r["source_url"]))
    if not sources:
        return _escalate(ctx, lead_id, "no usable public source to personalise from")
    source_urls = {s.id: s.url or "" for s in sources}
    source_texts = {s.id: s.content for s in sources}
    first_name = name.split()[0]
    data = {
        "language": cfg.language,
        "follow_ups": cfg.sequence.follow_ups,
        "prospect": {"first_name": first_name, "title": r["title"]},
        "company": {"name": company, "short_name": short_name(company)},
        "signal": {
            "signal_type": r["signal_type"],
            "detected_at": r["detected_at"].isoformat() if r["detected_at"] else None,
        },
        "sender": {"first_name": cfg.brand_voice.sender_name.split()[0], "title": cfg.brand_voice.sender_title},
        "offer": {"value_proposition": cfg.brand_voice.value_proposition, "purpose": cfg.outreach_purpose},
        "tone": cfg.brand_voice.tone,
        "banned_phrases": cfg.brand_voice.banned_phrases,
    }
    draft_prompt, critique_prompt = load_prompt(DRAFT), load_prompt(CRITIQUE)
    policy = load_prompt("common/untrusted_data_policy.v1.md").body
    wrapped = wrap_untrusted(sources)
    critique: WriterCritique | None = None
    issues: list[str] = []
    try:
        draft = ctx.llm.run_structured(
            prompt=draft_prompt,
            schema=WriterDraft,
            tenant_id=ctx.tenant_id,
            lead_id=lead_id,
            agent="writer",
            policy=policy,
            user_content=f"INPUT_JSON: {json.dumps(data)}\n{wrapped}",
        )
        for attempt in range(cfg.max_rewrites + 1):
            result = check_draft(
                draft,
                source_ids=set(source_urls),
                first_name=first_name,
                language=cfg.language,
                follow_ups_expected=cfg.sequence.follow_ups,
                banned_phrases=cfg.brand_voice.banned_phrases,
                source_texts=source_texts,
            )
            crit_in = {"draft": draft.model_dump(), "automated_checks": result.as_list(), "language": cfg.language}
            critique = ctx.llm.run_structured(
                prompt=critique_prompt,
                schema=WriterCritique,
                tenant_id=ctx.tenant_id,
                lead_id=lead_id,
                agent="writer.critique",
                policy=policy,
                user_content=f"INPUT_JSON: {json.dumps(crit_in)}",
            )
            quality = critique.overall if result.passed else min(critique.overall, 7.0)
            issues = result.as_list() + critique.issues
            if result.passed and quality >= cfg.min_quality_score:
                return _store(ctx, lead_id, r, draft, critique, quality, attempt, email, company, source_urls, inbox_id)
            if attempt == cfg.max_rewrites:
                break
            rewrite = {
                **data,
                "rewrite": {
                    "previous_draft": draft.model_dump(),
                    "issues": issues,
                    "critique": critique.rewrite_instructions,
                },
            }
            draft = ctx.llm.run_structured(
                prompt=draft_prompt,
                schema=WriterDraft,
                tenant_id=ctx.tenant_id,
                lead_id=lead_id,
                agent="writer",
                policy=policy,
                user_content=f"INPUT_JSON: {json.dumps(rewrite)}\n{wrapped}",
            )
    except LLMEscalation as exc:
        return _escalate(ctx, lead_id, f"llm: {exc.reason}", "llm_failure")
    return _escalate(ctx, lead_id, "quality below threshold after max rewrites: " + "; ".join(issues)[:500])


def _escalate(ctx: AgentContext, lead_id: uuid.UUID, why: str, reason: str = "other") -> WriteResult:
    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        pre = conn.execute(text("SELECT state FROM leads WHERE id = :l"), {"l": lead_id}).scalar_one()
        conn.execute(
            text("""INSERT INTO escalations (tenant_id, lead_id, reason, context, pre_escalation_state)
              VALUES (:t, :l, :r, CAST(:c AS jsonb), :p)"""),
            {
                "t": ctx.tenant_id,
                "l": lead_id,
                "r": reason,
                "c": json.dumps({"agent": "writer", "detail": why}),
                "p": pre,
            },
        )
        transition(conn, lead_id, State.ESCALATED, actor_type="agent", actor_id="writer", reason=why[:200])
    return WriteResult("escalated", reason=why)


def _store(
    ctx: AgentContext,
    lead_id: uuid.UUID,
    r: RowMapping,
    draft: WriterDraft,
    critique: WriterCritique,
    quality: float,
    rewrites: int,
    email: str,
    company: str,
    source_urls: dict[str, str],
    inbox_id: uuid.UUID | None,
) -> WriteResult:
    cfg = ctx.config
    tpl = FooterTemplate.load(config_root() / "footers" / f"{cfg.language}.yaml")
    try:
        footer = tpl.render(
            {
                "sender_name": cfg.brand_voice.sender_name,
                "sender_title": cfg.brand_voice.sender_title,
                "company_legal_name": r["legal_name"],
                "registration_jurisdiction": cfg.registration_jurisdiction,
                "company_number": r["company_number"] or "",
                "registered_office_address": r["registered_address"] or "",
                "data_source_summary": cfg.data_source_summary,
                "purpose": cfg.outreach_purpose,
                "privacy_url": f"{ctx.settings.public_base_url}/privacy/{r['slug']}",
            }
        )
    except FooterError as exc:
        return _escalate(ctx, lead_id, f"cannot build compliance footer: {exc}")
    skey = ctx.settings.key("suppression_hash_key")
    psources = [
        PersonalizationSource(claim=c.text, source_id=c.source_id, source_url=source_urls[c.source_id])
        for c in draft.claims
    ]
    # A/B: a stable variant per lead (the same lead always gets the same subject, retries included).
    variant = lead_id.int % ctx.config.sequence.subject_variants
    chosen = draft.subject_lines[variant]
    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        ids = []
        for step, (subject, body) in enumerate(
            [
                (chosen, draft.email_body),
                *[(f"Re: {chosen}", fu) for fu in draft.follow_ups],
            ]
        ):
            mid = create_draft(
                conn,
                cipher=ctx.cipher,
                suppression_key=skey,
                tenant_id=ctx.tenant_id,
                lead_id=lead_id,
                campaign_id=ctx.campaign_id,
                inbox_id=inbox_id,
                recipient_email=email,
                subject=subject,
                body=body,
                footer=footer,
                sequence_step=step,
                language=draft.language,
                quality_score=round(quality, 1),
                prompt_version="writer.draft.v2",
            )
            conn.execute(
                text("""UPDATE messages SET status = 'pending_approval', critique = CAST(:cr AS jsonb),
                  sources_snapshot = CAST(:ss AS jsonb), subject_options_enc = :so, subject_variant = :sv
                  WHERE id = :id"""),
                {
                    "cr": json.dumps(
                        {"scores": critique.scores.model_dump(), "issues": critique.issues, "rewrites": rewrites}
                    ),
                    "ss": json.dumps([{"id": k, "url": v} for k, v in source_urls.items()]),
                    "so": ctx.cipher.encrypt(
                        conn, ctx.tenant_id, "messages.subject_options", json.dumps(draft.subject_lines)
                    )
                    if step == 0
                    else None,
                    "sv": variant if step == 0 and ctx.config.sequence.subject_variants > 1 else None,
                    "id": mid,
                },
            )
            ids.append(mid)
        bodies = [draft.email_body, *draft.follow_ups]
        for mid, body in zip(ids, bodies, strict=True):
            for ps in psources:  # attach each claim to every message whose text contains it
                if ps.claim.lower() in body.lower():
                    conn.execute(
                        text("""INSERT INTO message_personalization_sources (tenant_id, message_id, claim_text,
                          source_url, source_type, retrieved_at) VALUES (:t, :m, :c, :u, 'signal', now())"""),
                        {"t": ctx.tenant_id, "m": mid, "c": ps.claim, "u": ps.source_url},
                    )
        transition(
            conn,
            lead_id,
            State.PENDING_APPROVAL,
            actor_type="agent",
            actor_id="writer",
            reason=f"draft ready (quality {quality:.1f}, rewrites {rewrites})",
        )
        write_audit(
            conn,
            action="writer.draft_ready",
            actor_type="agent",
            actor_id="writer",
            tenant_id=ctx.tenant_id,
            entity_type="message",
            entity_id=ids[0],
            after={"quality": quality, "rewrites": rewrites, "claims": len(psources)},
        )
    out = WriterOutput(
        language=draft.language,
        subject_lines=draft.subject_lines,
        email_body=draft.email_body,
        follow_ups=draft.follow_ups,
        compliance_footer=footer,
        personalization_sources=psources,
        quality_score=round(quality, 1),
        critique=critique.model_dump(),
        rewrites=rewrites,
    )
    return WriteResult("pending_approval", out, ids[0])
