# ADR-0010 — Phase 3: agent design, LLM simulator, approval desk

**Status:** Proposed (recorded at end of Phase 3)

| Decision | Why | Reversible |
|---|---|---|
| Paid LLM calls follow the three-key rule (adapter live + tenant live + GLOBAL_GO_LIVE with checklist signed); otherwise a deterministic **simulator** answers, recorded as model `simulated` | Brief: no paid API calls unless live + go-live approved. Lets the full pipeline, tests and demo run with zero spend and no key | Yes — evals against a real model need your explicit approval of a non-live exception, or go-live |
| Agent 2 is deterministic (no LLM) | Picking a persona match, verifying email and classifying legal form are rule-based; an LLM adds cost and risk without benefit | Yes |
| Persona titles match on whole words | Substring matching made "cto" match "Director" (found by tests) | — |
| Three-layer injection defence: sentence-level sanitiser before any model call; escaped `<untrusted_source>` blocks; deterministic output gate (no links/emails/phones, cited and source-supported claims only) | Defence in depth; the sanitiser alone or the prompt alone is not enough | Yes |
| Writer gate is deterministic and blocking; the model's self-score cannot override it (quality capped at 7 when any check fails) | The model grading itself is not a control | — |
| Claim grounding = verbatim in the email + cited source id exists + every figure and ≥70% of content words appear in the source | Found by the eval: a cited but invented claim previously passed | Yes (threshold tunable) |
| Approval decision covers the whole sequence (opener + follow-ups) in one action | Keeps review under 30 s; follow-ups still need approval | Yes |
| Names masked in the queue, including inside the email text; editing requires a logged unmask; edits containing masked tokens are refused | Masking by default (Tunisia access) without corrupting emails | — |
| Follow-ups scheduled N recipient-local business days after the opener and cancelled automatically unless the lead is still `contacted` | No follow-up after a reply, objection or suppression | — |
| Demo-scale synchronous `/pipeline/run` and `/send/run` endpoints, refused in staging/prod | Demo convenience; production uses Celery workers (`lead.discovered` → `process_lead`, beat `send_due`) | Yes |
| Mock providers backed by the synthetic demo dataset; any non-mock provider mode raises until Phase 6 | Fails loudly instead of silently calling a real service | — |
