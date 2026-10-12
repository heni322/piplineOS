from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DecisionMaker(BaseModel):
    """Agent 2 output (brief: FUNCTIONAL REQUIREMENTS / Agent 2). Professional data only; every field sourced."""

    model_config = ConfigDict(extra="forbid")
    person_name: str
    title: str
    company: str
    country: str | None
    verified_email: str | None
    subscriber_type: Literal["corporate", "individual", "unknown"]
    linkedin_url: str | None = None  # only if supplied by a licensed provider; never scraped
    data_source: str
    collected_at: datetime
    allowed_channels: list[str] = []
    confidence_score: float = Field(ge=0, le=1)
