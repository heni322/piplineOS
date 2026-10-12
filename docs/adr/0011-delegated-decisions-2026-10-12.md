# ADR-0011 — Decisions taken under delegated authority (2026-10-12)

**Context.** The owner asked the engineering lead to "take the decisions as CEO" at the end of Phase 3.
**Boundary.** Delegation covers engineering and product decisions. It does NOT cover anything the brief reserves
for the owner personally: legal sign-off (solicitor review), ICO registration, VAT setup, credentials/spend, and
go-live. Those remain on the go-live checklist and can only be signed off by the owner (enforced in the database).

| # | Decision | Rationale | Reversible |
|---|---|---|---|
| 1 | **Accept R12** (`braces` ≤ 3.0.3 advisory via `eslint-config-next`) | Dev-time lint dependency only, never shipped, never processes untrusted input; no patched release exists | Yes — re-checked every phase; CI fails on critical, and on this item once a fix ships |
| 2 | **Real-model Agent 3/4 evals: approved in principle** as a capped, synthetic-data-only exception to the three-key rule | Only way to measure real copy/classification quality (R18). **On hold**: needs an Anthropic API key with a monthly spend cap added by the owner; no spend without it | Yes |
| 3 | **Proceed with Phase 4** (replies, booking, show rate) | Phase 3 met its plan; no blockers | n/a |
