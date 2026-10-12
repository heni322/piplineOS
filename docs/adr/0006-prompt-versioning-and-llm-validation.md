# ADR-0006 — Versioned prompt files and validated LLM output

**Status:** Proposed

## Decision
- Prompts live in `backend/prompts/<agent>/<name>.v<N>.md` with YAML front-matter: `id`, `version`, `model_tier` (`volume` / `reasoning`), `output_schema` (Python dotted path), `max_tokens`, `budget_gbp`, `changelog`.
- Prompt files are **immutable once used in production**; changes create `v<N+1>`. CI computes SHA-256 of each and fails if a previously released version's hash changes (hashes recorded in `prompts/LOCK.json`).
- Every LLM call writes `llm_calls`: tenant, lead, agent, prompt id + version + hash, model, input/output tokens, cost (GBP, from a price table in config), latency, attempt number, outcome (`ok`, `invalid_retry`, `refusal`, `timeout`, `escalated`), validation errors. Prompt and response bodies stored encrypted and only when `LLM_LOG_BODIES=true` (default true in dev with synthetic data, false in prod until approved).
- Output contract: Pydantic v2 models (`extra='forbid'`), obtained via structured output / tool schema. Invalid → re-ask with validation errors (max 2) → `escalated`. Never passed downstream unvalidated.
- **Untrusted content** is wrapped in delimited blocks (`<untrusted_source id="…">…</untrusted_source>`) with an instruction that it is data only; outputs may only reference source IDs that were provided (validator rejects unknown IDs → kills invented citations).
- Model tiers map to model IDs in config (defaults: `volume` → `claude-sonnet-5-5`, `reasoning` → `claude-opus-5-5`), so model changes are config, not code.
- Reviewer edits from the approval queue are stored as (draft, final, diff, reason) and exported as a dataset to inform the next prompt version; no automatic prompt self-modification.

## Reversibility
High.
