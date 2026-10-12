"""Prompt-injection defence for UNTRUSTED text (web pages, signals, replies).

Layer 1 (here): remove sentences that look like instructions to an AI before any model sees them, and flag them.
Layer 2: untrusted text is wrapped/escaped (app.llm.client.wrap_untrusted) and the prompts say it is data.
Layer 3: agents have no side-effecting tools; outputs are schema-validated and checked deterministically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?|rules)",
    r"disregard (all |any )?(the )?(previous|prior|above) ",
    r"\b(system|developer) (note|prompt|message|instruction)s?\b",
    r"\bas an ai\b|\bany ai reading\b|\bdear (ai|assistant|model|llm)\b",
    r"\byou are now\b|\bnew instructions?\b|\boverride\b",
    r"(give|assign|set) (this|the) (company|lead|signal) (a )?(relevance )?score",
    r"\b(reveal|print|show) (your|the) (system )?(prompt|instructions)\b",
    r"\b(wire|transfer) (a |the )?(deposit|payment|money|funds)\b",
    r"\bremove (me|them|this person) from (the )?suppression\b",
    r"<\s*/?\s*(system|assistant|untrusted_source)\b",
]
_RX = re.compile("|".join(PATTERNS), re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class Sanitised:
    text: str
    removed: tuple[str, ...]

    @property
    def injection_suspected(self) -> bool:
        return bool(self.removed)


def sanitise_untrusted(text: str) -> Sanitised:
    kept: list[str] = []
    removed: list[str] = []
    for sentence in _SENTENCE.split(text or ""):
        s = sentence.strip()
        if not s:
            continue
        (removed if _RX.search(s) else kept).append(s)
    return Sanitised(" ".join(kept), tuple(removed))


def looks_like_injection(text: str) -> bool:
    return bool(_RX.search(text or ""))
