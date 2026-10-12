"""Deterministic LLM SIMULATOR used when no live LLM is permitted (the default; see app.llm.factory).

It reads the same inputs a real model would (INPUT_JSON + <untrusted_source> blocks) and returns plausible,
source-grounded outputs so the pipeline and the demo work end to end without paid API calls. It is NOT a
quality benchmark for real models: every call it answers is recorded with model='simulated'.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from app.llm.client import BackendResponse

SIM_MODEL = "simulated"
_SRC = re.compile(r'<untrusted_source id="([^"]+)"[^>]*>\n(.*?)\n</untrusted_source>', re.DOTALL)
_INPUT = re.compile(r"INPUT_JSON:\s*(\{.*?\})\s*(?:\n<untrusted_source|\Z)", re.DOTALL)
BASE_SCORE = {
    "tender": 74,
    "job_posting": 64,
    "funding": 62,
    "leadership_change": 65,
    "expansion": 60,
    "product_launch": 63,
    "tech_change": 60,
    "other": 40,
}
TOPIC = {
    "tender": "the new contract",
    "job_posting": "the new role",
    "funding": "your growth plans",
    "leadership_change": "the platform plans",
    "expansion": "the expansion",
    "product_launch": "the new service",
    "tech_change": "the platform change",
    "other": "the recent news",
}
LOWER_FIRST = {"The", "A", "An", "Three", "Two", "Four", "Our", "We", "This"}


def _parse(messages: list[dict[str, str]]) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    content = messages[0]["content"]
    m = _INPUT.search(content)
    data = json.loads(m.group(1)) if m else {}
    sources = [(sid, html.unescape(txt)) for sid, txt in _SRC.findall(content)]
    return data, sources


def _first_sentence(text: str, max_words: int) -> str:
    """First sentence; if too long, cut at the last clause boundary (comma) that fits, never mid-phrase."""
    s = re.split(r"(?<=[.!?])\s+", text.strip())[0].rstrip(".!? ")
    if len(s.split()) <= max_words:
        return s
    head = " ".join(s.split()[:max_words])
    cut = head.rfind(",")
    return head[:cut] if cut > len(head) // 2 else head.rsplit(" ", 1)[0]


def _score(data: dict[str, Any], sources: list[tuple[str, str]]) -> dict[str, Any]:
    sid, text = sources[0]
    low = text.lower()
    icp = data.get("icp", {})
    hits = [k for k in icp.get("industry_keywords", []) if k.lower() in low]
    excluded = [k for k in icp.get("exclude_keywords", []) if k.lower() in low]
    st = data.get("signal", {}).get("signal_type", "other")
    score = 25 if not hits else BASE_SCORE.get(st, 40) + 7 * min(len(hits), 3)
    if any(t in low for t in ("service delivery", "service desk", "it operations", "chief technology")):
        score += 5
    if excluded:
        score -= 40
    score = max(0, min(100, score))
    reason = (
        f"{len(hits)} ICP keyword match(es) ({', '.join(hits[:3]) or 'none'}); a {st.replace('_', ' ')} "
        f"{'suggests a near-term capacity need' if score >= 60 else 'gives little evidence of need'}."
    )
    if excluded:
        reason += f" Excluded keyword(s): {', '.join(excluded)}."
    return {
        "relevance_score": score,
        "signal_summary": _first_sentence(text, 40) + ".",
        "reasoning": reason,
        "cited_source_ids": [sid],
    }


def _draft(data: dict[str, Any], sources: list[tuple[str, str]]) -> dict[str, Any]:
    first = data["prospect"]["first_name"]
    short = data["company"]["short_name"]
    topic = TOPIC.get(data.get("signal", {}).get("signal_type", "other"), "the recent news")
    sender = data["sender"]["first_name"]
    sid, text = sources[0]
    claim = _first_sentence(text, 30)
    lead_in = claim[0].lower() + claim[1:] if claim.split()[0] in LOWER_FIRST else claim
    vp = data["offer"]["value_proposition"].rstrip(".")
    body = (
        f"Hi {first},\n\nI saw that {lead_in}.\n\nThat kind of growth usually stretches the service desk, "
        f"especially out of hours. We provide {vp}.\n\nWould a one-page summary of how other providers use it "
        f"be useful?\n\n{sender}"
    )
    if len(re.findall(r"\b[\w'’-]+\b", body)) > 90:
        body = (
            f"Hi {first},\n\nI saw that {lead_in}.\n\nWe provide {vp}.\n\nWould a one-page summary be useful?"
            f"\n\n{sender}"
        )
    fus = [
        f"Hi {first}, bringing this back to the top of your inbox. If extra service desk capacity is on the radar "
        f"after {topic}, I can send that short summary. Would that help?",
        f"Hi {first}, last note from me on this. If the timing is wrong, that is completely fine and I will not "
        "follow up again. Shall I close the loop?",
    ][: int(data.get("follow_ups", 2))]
    return {
        "language": data.get("language", "en-GB"),
        "subject_lines": [
            f"{short} service desk capacity"[:60],
            f"Question about {topic}"[:60],
            f"Out-of-hours cover for {short}"[:60],
        ],
        "email_body": body,
        "follow_ups": fus,
        "claims": [{"text": claim, "source_id": sid}],
    }


def _critique(data: dict[str, Any]) -> dict[str, Any]:
    failed = [c for c in data.get("automated_checks", []) if c.startswith("BLOCKING")]
    s = 6.0 if failed else 8.6
    scores = dict.fromkeys(
        ("specificity", "relevance", "length", "tone", "spam_words", "compliance_elements", "factual_grounding"), s
    )
    return {
        "scores": scores,
        "overall": s,
        "issues": failed,
        "rewrite_instructions": "Fix: " + "; ".join(failed) if failed else "",
    }


class SimulatedBackend:
    def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        json_schema: dict[str, Any],
        timeout: float,
    ) -> BackendResponse:
        data, sources = _parse(messages)
        title = json_schema.get("title")
        if title == "TriggerScore":
            out = _score(data, sources)
        elif title == "WriterDraft":
            out = _draft(data, sources)
        elif title == "WriterCritique":
            out = _critique(data)
        else:
            raise ValueError(f"simulator has no behaviour for schema {title!r}")
        return BackendResponse(
            text=json.dumps(out), input_tokens=0, output_tokens=0, stop_reason="end_turn", model=SIM_MODEL
        )
