"""Offer & pricing setup: the parts of a client's configuration agreed in the onboarding call, without editing
JSON. A new client starts from a niche template (config/niches/*.yaml); an existing configuration keeps everything
else (targeting, playbook, calendar) and only the fields below change. Every save is a new configuration version;
changed criteria or pricing must be accepted again by the client before anything is billed (signoffs)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from app.domain.tenants.config import Criterion, Pricing, TenantConfig


class CriterionIn(BaseModel):
    question: str = Field(min_length=5, max_length=300)
    required: bool = True
    evidence_from: Literal["reply", "profile"] = "reply"


class PricingIn(BaseModel):
    setup_fee_minor: int = Field(default=0, ge=0, le=100_000_000)
    monthly_retainer_minor: int = Field(default=0, ge=0, le=100_000_000)
    per_meeting_minor: int = Field(default=0, ge=0, le=100_000_000)
    payment_terms_days: int = Field(default=14, ge=1, le=90)
    dispute_window_days: int = Field(default=14, ge=1, le=60)


class SetupIn(BaseModel):
    niche: str | None = Field(default=None, pattern=r"^[a-z0-9-]{2,60}$")  # required only for a new client
    sender_name: str = Field(min_length=2, max_length=100)
    sender_title: str = Field(min_length=2, max_length=100)
    value_proposition: str = Field(min_length=10, max_length=500)
    outreach_purpose: str = Field(min_length=5, max_length=200)
    criteria: list[CriterionIn] | None = Field(default=None, min_length=1, max_length=8)
    pricing: PricingIn


class SetupError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def templates(root: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in sorted((root / "niches").glob("*.yaml")):
        data = yaml.safe_load(p.read_text())
        out[p.stem] = {"title": data["title"], "config": data["config"]}
    return out


def _criterion_id(question: str, taken: set[str]) -> str:
    words = re.findall(r"[a-z]+", question.lower())
    base = "_".join(words[:3])[:26] or "criterion"
    cid, n = base, 2
    while cid in taken or len(cid) < 2:
        cid, n = f"{base}_{n}"[:30], n + 1
    taken.add(cid)
    return cid


def build(current: TenantConfig | None, template: dict[str, Any] | None, body: SetupIn, currency: str) -> TenantConfig:
    if current is not None:
        data = current.model_dump()
    elif template is not None:
        data = dict(template["config"])
    else:
        raise SetupError("NICHE_REQUIRED")
    old_voice = data.get("brand_voice") or {}
    data["brand_voice"] = old_voice | {
        "sender_name": body.sender_name.strip(),
        "sender_title": body.sender_title.strip(),
        "value_proposition": body.value_proposition.strip(),
    }
    data["outreach_purpose"] = body.outreach_purpose.strip()
    if body.criteria is not None:
        old = {c["question"].strip(): c for c in (data.get("qualification") or {}).get("criteria", [])}
        taken: set[str] = set()
        criteria = []
        for c in body.criteria:
            q = c.question.strip()
            if q in old:  # unchanged wording keeps its id and hint keywords
                kept = old[q] | {"required": c.required, "evidence_from": c.evidence_from}
                taken.add(kept["id"])
                criteria.append(kept)
            else:
                criteria.append(None)
        for i, c in enumerate(body.criteria):
            if criteria[i] is None:
                q = c.question.strip()
                criteria[i] = Criterion(
                    id=_criterion_id(q, taken), question=q, required=c.required, evidence_from=c.evidence_from
                ).model_dump()
        data["qualification"] = (data.get("qualification") or {"framework": "custom"}) | {"criteria": criteria}
    if not data.get("qualification"):
        raise SetupError("CRITERIA_REQUIRED")
    data["pricing"] = Pricing(currency=currency, **body.pricing.model_dump()).model_dump()
    return TenantConfig.model_validate(data)
