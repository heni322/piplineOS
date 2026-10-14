"""Prompt-injection defence for UNTRUSTED text (web pages, signals, replies).

Layer 1 (here): remove sentences that look like instructions to an AI before any model sees them, and flag them.
Layer 2: untrusted text is wrapped/escaped (app.llm.client.wrap_untrusted) and the prompts say it is data.
Layer 3: agents have no side-effecting tools; outputs are schema-validated and checked deterministically.
"""

from __future__ import annotations

import re
import unicodedata
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
    # the same override in the other EU languages we will serve
    r"ignorez (toutes )?(les )?(instructions|consignes)",
    r"oubliez (toutes )?(les |vos )?(instructions|consignes|r[eè]gles)",
    r"\bnouvelles? (instructions|consignes)\b",
    r"\b(message|note|instruction|invite) (du )?syst[eè]me\b",
    r"\ben tant qu'(ia|assistant|intelligence artificielle)\b|\b(chère|cher) (ia|assistant)\b",
    r"\b(à|a) (toute|chaque) (ia|intelligence artificielle)\b",
    r"\b(r[ée]v[ée]lez|affichez|montrez) (votre|le|vos) (prompt|instructions|consignes)\b",
    r"\b(virez|versez|transf[ée]rez) (un |l')?(acompte|paiement|argent|fonds)\b",
    r"\b(attribuez|donnez|mettez) (une |la )?(note|score) (de )?(100|maximale)\b",
    r"ignorier(e|en sie) (alle )?(vorherigen |bisherigen )?(anweisungen|instruktionen)",
    r"negeer (alle )?(vorige |eerdere )?(instructies|opdrachten)",
    r"ignora (todas )?(las )?instrucciones",
    r"ignora (tutte )?(le )?istruzioni",
    # Nordic: Norwegian, Finnish, Swedish
    r"ignorer (alle )?(tidligere |forrige )?(instruksjoner|instrukser|regler)",
    r"\bnye instruks(joner|er)\b",
    r"\btil (enhver|alle|hver) (ai|ki|kunstig intelligens)\b",
    r"\b(gi|sett) (dette|denne) (leadet|selskapet|leaden) (høyeste |full )?(poengsum|score)",
    r"(unohda|ohita) (kaikki )?(aiemmat |edelliset )?(ohjeet|säännöt)",
    r"\buudet ohjeet\b",
    r"\b(jokaiselle|kaikille)\b.{0,30}\bteko[äa]ly",
    r"ignorera (alla )?(tidigare |föregående )?(instruktioner|regler)",
    r"\bnya instruktioner\b",
    r"\btill (varje|alla) (ai|ki)\b",
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


def _normalise(text: str) -> str:
    """NFKC + drop invisible format characters (zero-width spaces/joiners, bidi controls) used to hide instructions."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text) if unicodedata.category(ch) != "Cf")


def sanitise_untrusted(text: str) -> Sanitised:
    kept: list[str] = []
    removed: list[str] = []
    for sentence in _SENTENCE.split(_normalise(text or "")):
        s = sentence.strip()
        if not s:
            continue
        (removed if _RX.search(s) else kept).append(s)
    return Sanitised(" ".join(kept), tuple(removed))


def looks_like_injection(text: str) -> bool:
    return bool(_RX.search(_normalise(text or "")))
