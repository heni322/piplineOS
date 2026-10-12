from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=3, max_length=400)
    source_id: str = Field(min_length=1, max_length=50)


class WriterDraft(BaseModel):
    """LLM output of writer.draft."""

    model_config = ConfigDict(extra="forbid", title="WriterDraft")
    language: str
    subject_lines: list[str] = Field(min_length=3, max_length=3)
    email_body: str = Field(min_length=20, max_length=2000)
    follow_ups: list[str] = Field(default_factory=list, max_length=3)
    claims: list[Claim] = Field(default_factory=list, max_length=10)


class CritiqueScores(BaseModel):
    model_config = ConfigDict(extra="forbid")
    specificity: float = Field(ge=0, le=10)
    relevance: float = Field(ge=0, le=10)
    length: float = Field(ge=0, le=10)
    tone: float = Field(ge=0, le=10)
    spam_words: float = Field(ge=0, le=10)
    compliance_elements: float = Field(ge=0, le=10)
    factual_grounding: float = Field(ge=0, le=10)


class WriterCritique(BaseModel):
    """LLM output of writer.critique."""

    model_config = ConfigDict(extra="forbid", title="WriterCritique")
    scores: CritiqueScores
    overall: float = Field(ge=0, le=10)
    issues: list[str] = Field(default_factory=list, max_length=20)
    rewrite_instructions: str = Field(default="", max_length=2000)


class PersonalizationSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str
    source_id: str
    source_url: str


class WriterOutput(BaseModel):
    """Agent 3 output (brief: FUNCTIONAL REQUIREMENTS / Agent 3)."""

    model_config = ConfigDict(extra="forbid")
    language: str
    subject_lines: list[str]
    email_body: str
    follow_ups: list[str]
    compliance_footer: str
    personalization_sources: list[PersonalizationSource]
    quality_score: float
    critique: dict[str, object]
    rewrites: int
