"""Smart company search: the AI plans the Google Places searches and ranks what the checks confirmed.

1. **Plan.** From the client's targeting (offer, ideal customer profile, excluded activities) and the country, the AI
   writes a few "activity + area" searches in the country's language ("administrateur de biens Lyon").
2. **Search and confirm.** Each search runs through the usual gated, capped Places search, and every business must
   still prove itself: register number on its own site, confirmed by the register, website check
   (`app.domain.paid.discovery`). The AI never confirms or adds a company.
3. **Rank.** The AI scores each confirmed company's fit with the targeting (0-100, with a one-sentence reason),
   reading only the registered name and what the company's own home page says about itself. Nothing Google Places
   returned is given to the AI (Google's terms); the page text is untrusted and wrapped (prompt-injection policy).

The ranking is advice for the operator, who still chooses which companies to add. Which AI runs is decided by
`app.llm.factory` (Gemini, the local model or the simulator): this module never calls a provider directly.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import replace

from pydantic import BaseModel, Field

from app.agents.injection import sanitise_untrusted
from app.core.logging import get_logger
from app.domain.paid.discovery import Discovered
from app.domain.tenants.config import TenantConfig
from app.llm.client import LLMClient, LLMEscalation, UntrustedSource, wrap_untrusted
from app.llm.prompts import load_prompt

log = get_logger(__name__)
AGENT = "discovery"
LANGUAGES = {"GB": "en", "FR": "fr"}  # the language searches are written in, per country with a register
COUNTRY_NAMES = {"GB": "United Kingdom", "FR": "France"}
RANK_BATCH = 10  # companies per ranking call
_QUERY_OK = re.compile(r"^[^\"<>{}\[\]|\\]{3,120}$")


class SearchPlan(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=6)
    reasoning: str = Field(max_length=600)


class Fit(BaseModel):
    ref: str
    fit_score: int = Field(ge=0, le=100)
    reason: str = Field(max_length=300)


class FitScores(BaseModel):
    scores: list[Fit]


def _offer(cfg: TenantConfig) -> dict[str, object]:
    return {
        "value_proposition": cfg.brand_voice.value_proposition,
        "outreach_purpose": cfg.outreach_purpose,
        "ideal_customer": cfg.icp.description,
        "industry_keywords": cfg.icp.industry_keywords,
        "excluded_keywords": cfg.icp.exclude_keywords,
    }


def clean_queries(queries: Sequence[str], limit: int, excluded: Sequence[str] = ()) -> list[str]:
    """Trimmed, de-duplicated searches of a sensible length, none naming an excluded activity, at most `limit`."""
    out: list[str] = []
    seen: set[str] = set()
    bad = [e.strip().lower() for e in excluded if e.strip()]
    for raw in queries:
        q = " ".join(str(raw).split())
        key = q.lower()
        if not _QUERY_OK.match(q) or key in seen or any(b in key for b in bad):
            continue
        seen.add(key)
        out.append(q)
    return out[:limit]


def plan_queries(
    llm: LLMClient, cfg: TenantConfig, *, country: str, tenant_id: uuid.UUID, max_queries: int
) -> list[str]:
    """Places searches for this client and country, written by the AI. Raises LLMEscalation when none is usable."""
    data = {
        "offer": _offer(cfg),
        "target_country": country,
        "target_country_name": COUNTRY_NAMES.get(country, country),
        "language": LANGUAGES.get(country, "en"),
        "max_searches": max_queries,
    }
    plan = llm.run_structured(
        prompt=load_prompt("discovery/plan.v1.md"),
        schema=SearchPlan,
        user_content=f"INPUT_JSON: {json.dumps(data, ensure_ascii=False)}",
        tenant_id=tenant_id,
        agent=AGENT,
    )
    queries = clean_queries(plan.queries, max_queries, cfg.icp.exclude_keywords)
    if not queries:
        raise LLMEscalation("no_usable_searches", "the AI proposed no usable search")
    log.info("smart_search.planned", queries=len(queries))
    return queries


def rank(
    llm: LLMClient,
    cfg: TenantConfig,
    found: Sequence[Discovered],
    descriptions: dict[str, str],
    *,
    tenant_id: uuid.UUID,
    language: str,
) -> list[Discovered]:
    """The confirmed companies with their fit score and reason, best first. A batch the AI cannot score keeps its
    companies unscored (shown last) rather than failing the search."""
    policy = load_prompt("common/untrusted_data_policy.v1.md").body
    prompt = load_prompt("discovery/rank.v1.md")
    scored: dict[str, Fit] = {}
    items = list(found)
    for start in range(0, len(items), RANK_BATCH):
        batch = {f"c{start + i + 1}": d for i, d in enumerate(items[start : start + RANK_BATCH])}
        data = {
            "offer": _offer(cfg),
            "language": language,
            "companies": [
                {"ref": ref, "registered_name": d.name, "domain": d.domain, "country": d.country}
                for ref, d in batch.items()
            ],
        }
        sources = [
            UntrustedSource(ref, sanitise_untrusted(descriptions[d.domain]).text, f"https://{d.domain}/")
            for ref, d in batch.items()
            if descriptions.get(d.domain)
        ]
        try:
            out = llm.run_structured(
                prompt=prompt,
                schema=FitScores,
                user_content=f"INPUT_JSON: {json.dumps(data, ensure_ascii=False)}\n" + wrap_untrusted(sources),
                tenant_id=tenant_id,
                agent=AGENT,
                policy=policy,
            )
        except LLMEscalation as exc:
            log.warning("smart_search.rank_escalated", reason=exc.reason, companies=len(batch))
            continue
        scored |= {s.ref: s for s in out.scores if s.ref in batch}
    refs = {f"c{i + 1}": d for i, d in enumerate(items)}
    ranked = [
        replace(d, fit_score=scored[ref].fit_score, fit_reason=scored[ref].reason.strip()) if ref in scored else d
        for ref, d in refs.items()
    ]
    return sorted(ranked, key=lambda d: -1 if d.fit_score is None else d.fit_score, reverse=True)
