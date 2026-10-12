from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SignalType = Literal[
    "job_posting", "leadership_change", "funding", "expansion", "product_launch", "tech_change", "tender", "other"
]


class TriggerScore(BaseModel):
    """LLM output for one signal."""

    model_config = ConfigDict(extra="forbid", title="TriggerScore")
    relevance_score: int = Field(ge=0, le=100)
    signal_summary: str = Field(min_length=10, max_length=400)
    reasoning: str = Field(min_length=10, max_length=800)
    cited_source_ids: list[str] = Field(min_length=1)


class TriggerSignal(BaseModel):
    """Agent 1 output (brief: FUNCTIONAL REQUIREMENTS / Agent 1)."""

    model_config = ConfigDict(extra="forbid")
    company: str
    domain: str | None
    country: str | None
    signal_type: SignalType
    signal_summary: str
    source_url: str
    detected_at: datetime
    relevance_score: int = Field(ge=0, le=100)
    reasoning: str
