"""Persists every LLM call to llm_calls (bodies encrypted, only when LLM_LOG_BODIES=true)."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.crypto import PiiCipher
from app.db.session import DbContext, transaction
from app.llm.client import CallRecord


def db_recorder(engine: Engine, cipher: PiiCipher, log_bodies: bool):  # type: ignore[no-untyped-def]
    def record(r: CallRecord) -> None:
        with transaction(DbContext.system(r.tenant_id), engine) as conn:
            req = cipher.encrypt(conn, r.tenant_id, "llm_calls.request", r.request_text) if log_bodies else None
            resp = cipher.encrypt(conn, r.tenant_id, "llm_calls.response", r.response_text) if log_bodies else None
            conn.execute(
                text("""INSERT INTO llm_calls (tenant_id, lead_id, agent, prompt_id, prompt_version, prompt_hash,
                  model, input_tokens, output_tokens, cost_micros_gbp, latency_ms, attempt, outcome, validation_errors,
                  request_enc, response_enc) VALUES (:t, :l, :a, :pid, :pv, :ph, :m, :ti, :to, :c, :lat, :att, :o,
                  CAST(:ve AS jsonb), :rq, :rs)"""),
                {
                    "t": r.tenant_id,
                    "l": r.lead_id,
                    "a": r.agent,
                    "pid": r.prompt.id,
                    "pv": r.prompt.version,
                    "ph": r.prompt.sha256,
                    "m": r.model,
                    "ti": r.input_tokens,
                    "to": r.output_tokens,
                    "c": r.cost_micros_gbp,
                    "lat": r.latency_ms,
                    "att": r.attempt,
                    "o": r.outcome,
                    "ve": json.dumps(r.validation_errors) if r.validation_errors else None,
                    "rq": req,
                    "rs": resp,
                },
            )

    return record
