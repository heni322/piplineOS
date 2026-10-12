"""Agent 3 evaluation harness.

Part A — 40 synthetic prospect/source bundles through the writer's generation + deterministic gate + footer.
  With the default (simulated) backend this measures the PIPELINE guarantees, not real-model copy quality.
  Run against a live model only when all three live keys are on (see app.llm.factory).
Part B — guard precision/recall on a labelled set of flawed and clean drafts (tests/evals/datasets/writer_guard.jsonl).
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from pathlib import Path

from app.agents.writer.checks import US_TO_UK, check_draft, words
from app.agents.writer.schemas import WriterDraft
from app.demo.dataset import DEMO_CONFIG
from app.domain.outbound.footer import FooterTemplate, finalise_body
from app.llm.client import UntrustedSource, wrap_untrusted
from app.llm.simulator import SimulatedBackend

ROOT = Path(__file__).resolve().parents[3]
COMPANIES = ["Alder", "Birch", "Cedar", "Damson", "Elm", "Fir", "Gorse", "Hazel", "Iris", "Juniper"]
FACTS = {
    "job_posting": "{c} Managed IT is hiring a Service Desk Manager to support 60 business clients across Yorkshire.",
    "tender": "{c} IT Services Ltd won a 2-year contract to run the IT service desk for a housing association.",
    "funding": "{c} Cloud raised £1.5m to expand its 24/7 managed support team in Bristol.",
    "leadership_change": "{c} Networks appointed a new Head of Service Delivery to lead a service desk rebuild.",
}
GUARD_SOURCE = (
    "Contoso Managed IT is hiring a Head of Service Delivery to lead a team of 25 engineers supporting 120 SME clients."
)
FIRST = ["Alex", "Sam", "Jordan", "Riley", "Casey", "Morgan", "Jamie", "Taylor", "Drew", "Robin"]


@dataclass
class EvalResult:
    n: int
    unsourced_claims: int
    footer_missing: int
    over_90_words: int
    wrong_language: int
    us_spellings: int
    gate_failures: int


def run_part_a() -> EvalResult:
    sim = SimulatedBackend()
    tpl = FooterTemplate.load(ROOT / "config" / "footers" / "en-GB.yaml")
    footer = tpl.render(
        {
            "sender_name": "Jordan Lee",
            "sender_title": "Partnerships",
            "company_legal_name": "Northwind Ltd",
            "registration_jurisdiction": "England & Wales",
            "company_number": "01234567",
            "registered_office_address": "1 Example St",
            "data_source_summary": "a licensed provider",
            "purpose": "service desk support",
            "privacy_url": "https://example.test/privacy/x",
        }
    )
    res = EvalResult(0, 0, 0, 0, 0, 0, 0)
    for i, (company, (stype, fact)) in enumerate(itertools.product(COMPANIES, FACTS.items())):
        data = {
            "language": "en-GB",
            "follow_ups": 2,
            "prospect": {"first_name": FIRST[i % 10], "title": "Head of IT"},
            "company": {"name": f"{company} Ltd", "short_name": company},
            "signal": {"signal_type": stype},
            "sender": {"first_name": "Jordan"},
            "offer": {"value_proposition": DEMO_CONFIG.brand_voice.value_proposition},
        }
        user = f"INPUT_JSON: {json.dumps(data)}\n" + wrap_untrusted([UntrustedSource("s1", fact.format(c=company))])
        out = sim.complete(
            model="m",
            system="",
            messages=[{"role": "user", "content": user}],
            max_tokens=1,
            json_schema={"title": "WriterDraft"},
            timeout=1,
        )
        d = WriterDraft.model_validate_json(out.text)
        r = check_draft(
            d,
            source_ids={"s1"},
            first_name=FIRST[i % 10],
            language="en-GB",
            follow_ups_expected=2,
            banned_phrases=DEMO_CONFIG.brand_voice.banned_phrases,
            source_texts={"s1": fact.format(c=company)},
        )
        final = finalise_body(d.email_body, footer, "https://u.test/u/tok", "https://example.test/privacy/x")
        res.n += 1
        res.unsourced_claims += sum(1 for c in d.claims if c.source_id != "s1") + sum(
            1 for b in r.blocking if "unsourced" in b
        )
        res.footer_missing += int("Registered in" not in final or "https://u.test/u/tok" not in final)
        res.over_90_words += int(words(d.email_body) > 90)
        res.wrong_language += int(d.language != "en-GB")
        res.us_spellings += sum(1 for us in US_TO_UK if us in final.lower())
        res.gate_failures += int(not r.passed)
    return res


def run_part_b() -> tuple[int, int, int, int]:
    """Returns (true_positives, false_negatives, true_negatives, false_positives)."""
    tp = fn = tn = fp = 0
    for line in (Path(__file__).parent / "datasets" / "writer_guard.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        d = WriterDraft.model_validate(case["draft"])
        r = check_draft(
            d,
            source_ids={"s1"},
            first_name="Alex",
            language="en-GB",
            follow_ups_expected=2,
            banned_phrases=["game-changer"],
            source_texts={"s1": GUARD_SOURCE},
        )
        flawed = case["label"] == "flawed"
        if flawed and not r.passed:
            tp += 1
        elif flawed:
            fn += 1
        elif r.passed:
            tn += 1
        else:
            fp += 1
    return tp, fn, tn, fp


if __name__ == "__main__":
    a = run_part_a()
    tp, fn, tn, fp = run_part_b()
    print(
        json.dumps(
            {
                "part_a": a.__dict__,
                "part_b": {
                    "recall_on_flawed": tp / max(1, tp + fn),
                    "false_positives": fp,
                    "flawed": tp + fn,
                    "clean": tn + fp,
                },
            },
            indent=2,
        )
    )
