"""Real-model check with a FREE local model (Ollama on this machine): the real prompts, schemas, retries and
deterministic checks, with a real model answering instead of the simulator. Synthetic data only.

Part 1: Agent 4 (replies) on the 75 labelled synthetic replies.
Part 2: the demo pipeline (Agents 1-3 + compliance) on a FRESH dev database, compared with the expected outcomes.

Usage, from backend/ with DATABASE_URL / DATABASE_OWNER_URL pointing at an empty, migrated dev database:
    uv run python -m tests.evals.local_model_run --model qwen2.5:7b --out ../docs/local-model-eval.json
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.crypto import PiiCipher
from app.db.session import DbContext, get_engine, transaction
from app.demo.dataset import EXPECTED
from app.demo.seed import providers, seed_demo
from app.llm.backends import OllamaBackend
from app.settings import get_settings
from app.workflows.pipeline import agent_context, run_campaign
from tests.evals import reply_eval


def part_replies(backend: OllamaBackend) -> dict[str, Any]:
    t0 = time.monotonic()
    r = reply_eval.run(backend)
    return {
        "replies": r.n,
        "accuracy": round(r.accuracy(), 3),
        "recall": {k: round(r.recall(k), 3) for k in sorted({g for g, _ in r.confusion})},
        "safety_recall": {k: round(r.safety_recall(k), 3) for k in reply_eval.SAFETY},
        "injection": {
            "total": r.injection_total,
            "escalated": r.injection_escalated,
            "unsafe": r.injection_unsafe_action,
        },
        "misses": r.misses[:25],
        "seconds": round(time.monotonic() - t0),
    }


def part_pipeline(backend: OllamaBackend) -> dict[str, Any]:
    settings = get_settings()
    if settings.environment in ("staging", "prod"):
        raise SystemExit("dev/test databases only")
    engine, cipher = get_engine(), PiiCipher(settings.key("pii_master_key"))
    t0 = time.monotonic()
    ids = seed_demo(engine, settings)
    ctx = agent_context(engine, settings, cipher, ids.tenant_id, ids.campaign_id, llm_backend=backend)
    run = run_campaign(ctx, providers())
    with transaction(DbContext.system(ids.tenant_id), engine) as conn:
        states = {
            r.n: r.state
            for r in conn.execute(
                text(
                    "SELECT coalesce(c.name, 'Sam Patel IT Consulting') AS n, l.state FROM leads l "
                    "JOIN companies c ON c.id = l.company_id"
                )
            )
        }
        drafts = conn.execute(
            text("""SELECT c.name, m.subject_enc, m.body_enc, m.quality_score FROM messages m
                    JOIN leads l ON l.id = m.lead_id JOIN companies c ON c.id = l.company_id
                    WHERE m.sequence_step = 0 ORDER BY c.name""")
        ).all()
        calls = conn.execute(
            text("""SELECT agent, outcome, count(*) AS n, avg(latency_ms)::int AS ms, sum(output_tokens) AS out_tok
                    FROM llm_calls GROUP BY agent, outcome ORDER BY agent, outcome""")
        ).all()
        samples = [
            {
                "company": d.name,
                "quality": float(d.quality_score) if d.quality_score is not None else None,
                "subject": cipher.decrypt(conn, ids.tenant_id, "messages.subject", d.subject_enc)
                if d.subject_enc
                else None,
                "body": cipher.decrypt(conn, ids.tenant_id, "messages.body", d.body_enc) if d.body_enc else None,
            }
            for d in drafts
        ]
    compared = {name: {"expected": want, "got": states.get(name)} for name, want in EXPECTED.items()}
    return {
        "signals": {
            "fetched": run.hunt.fetched,
            "discovered": run.hunt.discovered,
            "below_threshold": run.hunt.below_threshold,
            "deduplicated": run.hunt.deduplicated,
            "escalated": run.hunt.escalated,
            "injection_flags": run.hunt.injection_flags,
        },
        "outcomes_match": sum(v["expected"] == v["got"] for v in compared.values()),
        "outcomes_total": len(compared),
        "outcomes": compared,
        "drafts": len(samples),
        "quality_scores": sorted(s["quality"] for s in samples if s["quality"] is not None),
        "draft_samples": samples,
        "llm_calls": [
            {"agent": c.agent, "outcome": c.outcome, "n": c.n, "avg_ms": c.ms, "output_tokens": c.out_tok}
            for c in calls
        ],
        "outcome_counts": dict(Counter(states.values())),
        "seconds": round(time.monotonic() - t0),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=get_settings().local_llm_model)
    ap.add_argument("--url", default=get_settings().local_llm_url)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--skip-replies", action="store_true")
    ap.add_argument("--skip-pipeline", action="store_true")
    a = ap.parse_args()
    backend = OllamaBackend(a.url, a.model)
    out: dict[str, Any] = {"model": a.model, "started": time.strftime("%Y-%m-%d %H:%M")}
    if not a.skip_replies:
        out["replies"] = part_replies(backend)
        a.out.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print("replies done", json.dumps({k: v for k, v in out["replies"].items() if k != "misses"}), flush=True)
    if not a.skip_pipeline:
        out["pipeline"] = part_pipeline(backend)
        print("pipeline done", out["pipeline"]["outcomes_match"], "/", out["pipeline"]["outcomes_total"], flush=True)
    a.out.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
