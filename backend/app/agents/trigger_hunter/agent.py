"""Agent 1 — Trigger Hunter: official/licensed signal sources -> ICP relevance (0-100) -> leads 'discovered'."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import Connection, text

from app.adapters.base import RateLimited, TokenBucket
from app.adapters.insolvency import InsolvencyCheck
from app.adapters.signals.base import RawSignal, SignalSource
from app.agents.base import AgentContext
from app.agents.injection import sanitise_untrusted
from app.agents.trigger_hunter.schemas import TriggerScore, TriggerSignal
from app.core.audit import write_audit
from app.core.crypto import normalise_domain
from app.core.logging import get_logger
from app.db.session import DbContext, transaction
from app.domain.compliance.jurisdiction import parse_location
from app.domain.sources.health import describe_error, record_run
from app.llm.client import LLMEscalation, LLMUnavailable, UntrustedSource, wrap_untrusted
from app.llm.prompts import PromptFile, load_prompt

log = get_logger(__name__)
PROMPT = "trigger_hunter/score.v1.md"


@dataclass
class HuntStats:
    fetched: int = 0
    deduplicated: int = 0
    below_threshold: int = 0
    discovered: int = 0
    escalated: int = 0
    rate_limited: int = 0
    injection_flags: int = 0
    insolvent: int = 0
    bad_source_url: int = 0
    source_failures: int = 0
    deferred: int = 0  # not scored because the model was unavailable; seen again on the next run
    signals: list[TriggerSignal] = field(default_factory=list)


@dataclass
class SourceBinding:
    source: SignalSource
    query: str
    limiter: TokenBucket = field(default_factory=lambda: TokenBucket(rate_per_sec=5, capacity=20))


def _bucket(detected_at: datetime, window_days: int) -> int:
    return int(detected_at.timestamp() // (window_days * 86400))


def hunt(
    ctx: AgentContext, bindings: list[SourceBinding], since: datetime, insolvency: InsolvencyCheck | None = None
) -> HuntStats:
    stats = HuntStats()
    prompt = load_prompt(PROMPT)
    policy = load_prompt("common/untrusted_data_policy.v1.md").body
    cfg = ctx.config
    for b in bindings:
        started = datetime.now(UTC)
        try:
            b.limiter.acquire_or_raise()
            raw = b.source.fetch(b.query, since)
        except RateLimited:
            stats.rate_limited += 1  # nothing lost: the next scheduled run fetches again from `since`
            record_run(ctx.engine, b.source.name, started_at=started, outcome="rate_limited")
            continue
        except Exception as exc:
            stats.source_failures += 1
            log.warning("signal_source_failed", source=b.source.name, error=describe_error(exc))
            record_run(ctx.engine, b.source.name, started_at=started, outcome="failed", error=describe_error(exc))
            continue
        failed_parts = list(getattr(b.source, "failed", []) or [])
        metrics = dict(getattr(b.source, "metrics", {}) or {})  # e.g. the crawler's pages read, ads rejected by reason
        detail = ({"failed": failed_parts[:20]} if failed_parts else {}) | ({"metrics": metrics} if metrics else {})
        record_run(
            ctx.engine,
            b.source.name,
            started_at=started,
            outcome="partial" if failed_parts else "ok",
            fetched=len(raw),
            detail=detail or None,
        )
        for s in sorted(raw, key=lambda r: r.detected_at):
            stats.fetched += 1
            _process(
                ctx, prompt, policy, b.source.name, s, stats, cfg.signal_threshold, cfg.dedupe_window_days, insolvency
            )
    return stats


def _existing_company(conn: Connection, domain: str | None, name: str, number: str | None) -> uuid.UUID | None:
    """The tenant's existing record for this company: by domain; for sources without a domain (procurement notices),
    by Companies House number, then by exact name (case-insensitive)."""
    if domain:
        by_domain = conn.execute(text("SELECT id FROM companies WHERE domain_normalised = :d"), {"d": domain}).scalar()
        return uuid.UUID(str(by_domain)) if by_domain else None
    if number:
        found = conn.execute(
            text("SELECT id FROM companies WHERE company_number = :n ORDER BY created_at LIMIT 1"), {"n": number}
        ).scalar()
        if found:
            return uuid.UUID(str(found))
    by_name = conn.execute(
        text("SELECT id FROM companies WHERE lower(name) = lower(:n) ORDER BY created_at LIMIT 1"), {"n": name}
    ).scalar()
    return uuid.UUID(str(by_name)) if by_name else None


def _process(
    ctx: AgentContext,
    prompt: PromptFile,
    policy: str,
    source_name: str,
    s: RawSignal,
    stats: HuntStats,
    threshold: int,
    window_days: int,
    insolvency: InsolvencyCheck | None = None,
) -> None:
    if urlsplit(s.source_url).scheme not in ("https", "http"):  # source data: never store a javascript: link
        stats.bad_source_url += 1
        return
    domain = normalise_domain(s.domain) if s.domain else None
    loc = parse_location(s.country_hint)
    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        company_id = _existing_company(conn, domain, s.company_name, s.company_number)
        if company_id is not None:
            recent = conn.execute(
                text("SELECT count(*) FROM signals WHERE company_id = :c AND detected_at > :since"),
                {"c": company_id, "since": s.detected_at - timedelta(days=window_days)},
            ).scalar_one()
            if recent:
                stats.deduplicated += 1
                return
    if insolvency is not None and s.company_number:  # before any AI is spent on it
        why = insolvency.is_insolvent(s.company_number)
        if why:
            stats.insolvent += 1
            log.info("trigger_skipped_insolvent", source=source_name, reason=why.split(":", 1)[0])
            return
    clean = sanitise_untrusted(f"{s.text} (Headline: {s.title})")
    if clean.injection_suspected:
        stats.injection_flags += 1
    data = {
        "icp": ctx.config.icp.model_dump(),
        "signal": {
            "company": s.company_name,
            "signal_type": s.signal_type,
            "detected_at": s.detected_at.isoformat(),
            "source": source_name,
        },
    }
    user = f"INPUT_JSON: {json.dumps(data)}\n" + wrap_untrusted([UntrustedSource("s1", clean.text, s.source_url)])
    try:
        score = ctx.llm.run_structured(
            prompt=prompt,
            schema=TriggerScore,
            user_content=user,
            tenant_id=ctx.tenant_id,
            agent="trigger_hunter",
            policy=policy,
        )
    except LLMUnavailable:  # the model is down: the signal is fetched again (lookback window) on the next run
        stats.deferred += 1
        return
    except LLMEscalation as exc:
        stats.escalated += 1
        log.warning("trigger_scoring_escalated", reason=exc.reason, source=source_name)
        return
    sig = TriggerSignal(
        company=s.company_name,
        domain=domain,
        country=loc.country,
        signal_type=s.signal_type,
        signal_summary=score.signal_summary,
        source_url=s.source_url,
        detected_at=s.detected_at,
        relevance_score=score.relevance_score,
        reasoning=score.reasoning,
    )
    stats.signals.append(sig)
    dedupe_key = hashlib.sha256(
        f"{domain or s.company_name.lower()}|{_bucket(s.detected_at, window_days)}".encode()
    ).hexdigest()
    with transaction(DbContext.system(ctx.tenant_id), ctx.engine) as conn:
        company_id = _existing_company(conn, domain, s.company_name, s.company_number) if domain is None else None
        if company_id is None:
            company_id = conn.execute(
                text("""
                INSERT INTO companies (tenant_id, name, domain, domain_normalised, country_code, industry_codes,
                  company_number)
                VALUES (:t, :n, :d, :d, :c, '{}', :cn)
                ON CONFLICT (tenant_id, domain_normalised) WHERE domain_normalised IS NOT NULL
                DO UPDATE SET updated_at = now() RETURNING id"""),
                {"t": ctx.tenant_id, "n": s.company_name, "d": domain, "c": loc.country, "cn": s.company_number},
            ).scalar_one()
        signal_id = conn.execute(
            text("""
            INSERT INTO signals (tenant_id, company_id, campaign_id, source_adapter, source_url, signal_type,
              signal_summary, detected_at, relevance_score, reasoning, raw_ref, dedupe_key)
            VALUES (:t, :c, :cp, :src, :url, :st, :sum, :at, :score, :why, :raw, :dk)
            ON CONFLICT (tenant_id, dedupe_key) DO NOTHING RETURNING id"""),
            {
                "t": ctx.tenant_id,
                "c": company_id,
                "cp": ctx.campaign_id,
                "src": source_name,
                "url": s.source_url,
                "st": s.signal_type,
                "sum": score.signal_summary,
                "at": s.detected_at,
                "score": score.relevance_score,
                "why": score.reasoning
                + (" [injection attempt removed from source]" if clean.injection_suspected else ""),
                "raw": s.source_record_id,
                "dk": dedupe_key,
            },
        ).scalar()
        if signal_id is None:
            stats.deduplicated += 1
            return
        if score.relevance_score < threshold:
            stats.below_threshold += 1
            return
        lead_id = conn.execute(
            text("""INSERT INTO leads (tenant_id, campaign_id, company_id, signal_id)
              VALUES (:t, :cp, :c, :s) RETURNING id"""),
            {"t": ctx.tenant_id, "cp": ctx.campaign_id, "c": company_id, "s": signal_id},
        ).scalar_one()
        write_audit(
            conn,
            action="lead.discovered",
            actor_type="agent",
            actor_id="trigger_hunter",
            tenant_id=ctx.tenant_id,
            entity_type="lead",
            entity_id=lead_id,
            after={"signal_id": str(signal_id), "score": score.relevance_score},
        )
        conn.execute(
            text("INSERT INTO outbox (tenant_id, topic, payload) VALUES (:t, 'lead.discovered', CAST(:p AS jsonb))"),
            {"t": ctx.tenant_id, "p": json.dumps({"lead_id": str(lead_id)})},
        )
        stats.discovered += 1
