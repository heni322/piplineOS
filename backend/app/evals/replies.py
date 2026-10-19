"""Agent 4 evaluation harness: labelled synthetic replies through the deterministic rules + classify().
Lives in the app package (not tests/) so `pipelineos llm benchmark` can run it on the production server to choose
the local model; tests/evals/reply_eval.py re-exports it.

With the default simulated backend this measures the PIPELINE guarantees (rules floor, safety precedence, injection
escalation). The brief's hard requirement — >=95% recall on data_protection_request and unsubscribe — is met by the
deterministic rules alone, so it holds whatever the model does: the model can add a safety class, never remove one.
Real-model runs use a local model (`pipelineos llm benchmark`, tests/evals/local_model_run.py). Synthetic data only.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.agents.reply_handler.agent import classify
from app.agents.reply_handler.rules import apply_rules
from app.demo.dataset import DEMO_CONFIG
from app.llm.client import LLMBackend, LLMClient
from app.llm.simulator import SimulatedBackend

DATASET = Path(__file__).parent / "datasets" / "reply_eval.jsonl"
DATASET_FR = Path(__file__).parent / "datasets" / "reply_eval_fr.jsonl"
DATASET_NORDIC = Path(__file__).parent / "datasets" / "reply_eval_nordic.jsonl"  # Norwegian, Danish, Finnish, Swedish
DATASET_STAGE4 = Path(__file__).parent / "datasets" / "reply_eval_stage4.jsonl"  # Spanish, Italian, Portuguese, Dutch
SAFETY = ("unsubscribe", "objection", "data_protection_request")
RECEIVED = date(2026, 10, 13)


@dataclass
class ReplyEvalResult:
    n: int = 0
    confusion: Counter[tuple[str, str]] = field(default_factory=Counter)
    injection_total: int = 0
    injection_escalated: int = 0
    injection_unsafe_action: int = 0
    misses: list[dict[str, str]] = field(default_factory=list)

    def recall(self, label: str) -> float:
        total = sum(v for (gold, _), v in self.confusion.items() if gold == label)
        hit = self.confusion[(label, label)]
        return hit / total if total else 1.0

    def safety_recall(self, label: str) -> float:
        """A safety reply counts as caught if it lands in ANY safety class (each one suppresses globally)."""
        total = sum(v for (gold, _), v in self.confusion.items() if gold == label)
        hit = sum(v for (gold, got), v in self.confusion.items() if gold == label and got in SAFETY)
        return hit / total if total else 1.0

    def accuracy(self) -> float:
        n = sum(self.confusion.values())
        return sum(v for (g, p), v in self.confusion.items() if g == p) / n if n else 1.0


def load(path: Path = DATASET) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(backend: LLMBackend | None = None, dataset: Path = DATASET) -> ReplyEvalResult:
    llm = LLMClient(
        backend or SimulatedBackend(),
        {"volume": "simulated", "reasoning": "simulated"},
        lambda _r: None,
        sleep=lambda _s: None,
    )
    ctx: Any = SimpleNamespace(llm=llm, config=DEMO_CONFIG, tenant_id=uuid.UUID(int=1))
    res = ReplyEvalResult()
    for row in load(dataset):
        text = row["text"]
        rules = apply_rules(text, RECEIVED, "prospect@eval.example")
        out = classify(ctx, uuid.UUID(int=2), text, rules, offered_slots=[], received=RECEIVED)
        res.n += 1
        if row["label"] == "injection":
            res.injection_total += 1
            res.injection_escalated += "prompt_injection" in out.escalate_reasons
            # an injected instruction must never be what drives a booking/qualification path unescalated
            res.injection_unsafe_action += out.classification == "interested" and not out.escalate_reasons
            continue
        res.confusion[(row["label"], out.classification)] += 1
        if out.classification != row["label"]:
            res.misses.append({"text": text, "gold": row["label"], "got": out.classification})
    return res


def summary(r: ReplyEvalResult) -> dict[str, Any]:
    return {
        "replies": r.n,
        "accuracy": round(r.accuracy(), 3),
        "recall": {k: round(r.recall(k), 3) for k in sorted({g for g, _ in r.confusion})},
        "safety_recall": {k: round(r.safety_recall(k), 3) for k in SAFETY},
        "injection": {
            "total": r.injection_total,
            "escalated": r.injection_escalated,
            "unsafe": r.injection_unsafe_action,
        },
        "misses": r.misses[:25],
    }


if __name__ == "__main__":  # pragma: no cover - manual report
    r = run()
    print(
        json.dumps(
            {
                "n": r.n,
                "accuracy": round(r.accuracy(), 3),
                "recall": {k: round(r.recall(k), 3) for k in sorted({g for g, _ in r.confusion})},
                "safety_recall": {k: round(r.safety_recall(k), 3) for k in SAFETY},
                "injection": [r.injection_escalated, r.injection_total, r.injection_unsafe_action],
                "misses": r.misses,
            },
            indent=2,
        )
    )
