"""Deterministic quality and compliance checks on a writer draft. Any BLOCKING issue prevents approval routing,
whatever score the model gives itself."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agents.writer.schemas import WriterDraft

MAX_OPENER_WORDS = 90
MAX_FOLLOW_UP_WORDS = 60
MAX_SUBJECT_CHARS = 60

SPAM = [
    "free",
    "guarantee",
    "guaranteed",
    "risk-free",
    "risk free",
    "act now",
    "limited time",
    "urgent",
    "winner",
    "click here",
    "100%",
    "no obligation",
    "!!!",
    "cash",
    "double your",
    "once in a lifetime",
    # French
    "gratuit",
    "gratuite",
    "garanti",
    "garantie",
    "sans engagement",
    "offre limitée",
    "dernière chance",
    "cliquez ici",
    "100 %",
]
HIGH_FRICTION = [
    r"\b(15|20|30|45|60)[- ]?min",
    r"\bbook (a|an|some) (call|meeting|demo|slot)\b",
    r"\bcalendly\b",
    r"\bcalendar link\b",
    r"\bschedule (a|an) (call|demo|meeting)\b",
    r"\br[ée]server (un|une) (appel|cr[ée]neau|rendez-vous|d[ée]mo)\b",
    r"\bprendre (un )?rendez-vous\b",
]
SENSITIVE = [
    r"\b(wife|husband|partner's|children|kids|pregnan\w*|religio\w*|church|mosque|illness|diagnos\w*|"
    r"disabilit\w*|divorce\w*|birthday|sexual\w*|ethnic\w*|political view\w*)\b",
    r"\b(votre (femme|mari|conjoint\w*)|vos enfants|enceinte|religi\w*|[ée]glise|mosqu[ée]e|maladie|diagnostic\w*|"
    r"handicap\w*|divorc\w*|anniversaire|sexualit[ée]|ethni\w*|opinions? politiques?)\b",
]
INFORMAL_FR = re.compile(r"\b(tu|toi|ton|ta|tes|t'as|t'es)\b", re.IGNORECASE)
US_TO_UK = {
    "optimize": "optimise",
    "optimization": "optimisation",
    "organization": "organisation",
    "organize": "organise",
    "prioritize": "prioritise",
    "analyze": "analyse",
    "color": "colour",
    "center": "centre",
    "behavior": "behaviour",
    "favorite": "favourite",
    "realize": "realise",
    "recognize": "recognise",
    "customize": "customise",
    "standardize": "standardise",
    "modeling": "modelling",
    "enrollment": "enrolment",
    "fulfill": "fulfil",
    "utilize": "utilise",
    "minimize": "minimise",
    "maximize": "maximise",
    "summarize": "summarise",
    "specialize": "specialise",
    "labor": "labour",
}
URL = re.compile(r"(https?://|www\.)\S+|\b\S+\.(com|co\.uk|io|net|org|example)\b/?\S*", re.IGNORECASE)
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
PHONE = re.compile(r"(\+?\d[\d\s().-]{8,}\d)")
NUMBER = re.compile(r"(?<![\w£$€])[£$€]?\d+(?:[.,]\d+)*(?:m|k|bn|%)?(?![\w])")
MASK_TOKEN = re.compile(r"\b\w\*{3}")


@dataclass
class CheckResult:
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.blocking

    def as_list(self) -> list[str]:
        return [f"BLOCKING: {b}" for b in self.blocking] + [f"warning: {w}" for w in self.warnings]


def words(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower().rstrip(".")


def _uk_spelling_issues(text: str) -> list[str]:
    low = text.lower()
    return [f"US spelling '{us}' (use '{uk}')" for us, uk in US_TO_UK.items() if re.search(rf"\b{us}\w*\b", low)]


STOPWORDS = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "its",
        "their",
        "our",
        "your",
        "has",
        "have",
        "had",
        "are",
        "was",
        "were",
        "will",
        "been",
        "who",
        "which",
        "than",
        "over",
        "under",
        "about",
        "after",
        "before",
        "also",
        "just",
        "very",
        "more",
        "most",
        "such",
    ]
)


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9£$€][\w£$€'’-]*", text.lower()) if len(w) >= 3 and w not in STOPWORDS}


def claim_supported(claim: str, source_text: str, min_overlap: float = 0.7) -> bool:
    """Lexical grounding: every figure in the claim appears in the source, and most of its content words do."""
    src = source_text.lower()
    if any(n.lower() not in src for n in NUMBER.findall(claim)):
        return False
    cw = _content_words(claim)
    return not cw or len(cw & _content_words(source_text)) / len(cw) >= min_overlap


def check_draft(
    draft: WriterDraft,
    *,
    source_ids: set[str],
    first_name: str,
    language: str,
    follow_ups_expected: int,
    banned_phrases: list[str],
    source_texts: dict[str, str] | None = None,
) -> CheckResult:
    r = CheckResult()
    body, fus = draft.email_body, draft.follow_ups
    all_text = "\n".join([body, *fus])
    everything = "\n".join([*draft.subject_lines, all_text])

    if draft.language != language:
        r.blocking.append(f"language {draft.language!r} does not match {language!r}")
    n = words(body)
    if n > MAX_OPENER_WORDS:
        r.blocking.append(f"opener has {n} words (max {MAX_OPENER_WORDS})")
    for i, fu in enumerate(fus, 1):
        if words(fu) > MAX_FOLLOW_UP_WORDS:
            r.blocking.append(f"follow-up {i} has {words(fu)} words (max {MAX_FOLLOW_UP_WORDS})")
    if len(fus) != follow_ups_expected:
        r.blocking.append(f"expected {follow_ups_expected} follow-ups, got {len(fus)}")
    for s in draft.subject_lines:
        if len(s) > MAX_SUBJECT_CHARS:
            r.blocking.append(f"subject over {MAX_SUBJECT_CHARS} chars: {s[:30]}…")
        if s.isupper() and len(s) > 4:
            r.blocking.append("subject in ALL CAPS")

    # grounding: every claim cites a provided source and appears verbatim; numbers must be inside claims
    for c in draft.claims:
        if c.source_id not in source_ids:
            r.blocking.append(f"unsourced claim (unknown source id {c.source_id!r}): {c.text[:60]}")
        if _norm(c.text) not in _norm(all_text):
            r.blocking.append(f"claim not found verbatim in the email: {c.text[:60]}")
        if (
            source_texts is not None
            and c.source_id in source_texts
            and not claim_supported(c.text, source_texts[c.source_id])
        ):
            r.blocking.append(f"claim not supported by its source ({c.source_id}): {c.text[:60]}")
    if not any(_norm(c.text) in _norm(body) for c in draft.claims if c.source_id in source_ids):
        r.blocking.append("opener has no specific, sourced detail")
    claim_text = " ".join(_norm(c.text) for c in draft.claims if c.source_id in source_ids)
    for num in NUMBER.findall(all_text):
        if num.lower() not in claim_text:
            r.blocking.append(f"unsourced figure {num!r}")

    if URL.search(everything) or EMAIL.search(everything):
        r.blocking.append("links or email addresses are not allowed in the copy")
    if PHONE.search(everything):
        r.blocking.append("phone numbers are not allowed in the copy")
    if re.search(
        r"unsubscribe|privacy notice|registered in|company number|d[ée]sinscri|confidentialit[ée]|immatricul|siren",
        all_text,
        re.IGNORECASE,
    ):
        r.blocking.append("footer/legal text must not be written by the model (added automatically)")
    if MASK_TOKEN.search(everything):
        r.blocking.append("contains masked tokens (e.g. 'A***'): unmask before editing")
    low = everything.lower()
    for w in SPAM:
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low):
            r.blocking.append(f"spam trigger {w!r}")
    for p in banned_phrases:
        if p.lower() in low:
            r.blocking.append(f"banned phrase {p!r}")
    for p in HIGH_FRICTION:
        if re.search(p, body, re.IGNORECASE):
            r.blocking.append("high-friction call to action in the opener (ask a question instead)")
            break
    for p in SENSITIVE:
        if re.search(p, everything, re.IGNORECASE):
            r.blocking.append("mentions personal or sensitive details")
    if language == "en-GB":
        r.blocking.extend(_uk_spelling_issues(everything))
    if language.startswith("fr"):
        informal = sorted({m.group(1).lower() for m in INFORMAL_FR.finditer(everything)})
        if informal:
            r.blocking.append(f"informal address ({', '.join(informal)}): use the formal 'vous' in French")

    sentences = [s for s in re.split(r"(?<=[.!?])\s+", body.strip()) if s]
    if "?" not in body:
        r.blocking.append("opener has no question as its call to action")
    if first_name and first_name.lower() not in body.lower()[:60]:
        r.warnings.append("greeting does not use the prospect's first name")
    if sentences and body.count("!") > 1:
        r.warnings.append("more than one exclamation mark")
    return r
