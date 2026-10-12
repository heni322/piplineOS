# ADR-0004 — Single outbound gateway and three-key live switch

**Status:** Proposed

## Context
Dry-run must be the default everywhere. The compliance gate and suppression check must be impossible to bypass from any path: retries, admin tools, manual sends, the operator's own campaigns.

## Decision
1. **One gateway** (`app/domain/outbound/gateway.py`) owns every external side effect: email send, calendar create/update/cancel, CRM write, paid enrichment calls are routed through a sibling `PaidCallGuard`.
2. **Import-linter contract**: side-effecting adapter modules may be imported only by the gateway/guard. CI fails otherwise.
3. **Send-time re-check** in one transaction with a row lock: compliance (fresh decision), suppression, tenant gates (LIA, DPA, not paused), deliverability (inbox health, caps, local business hours, bank holidays), and mode resolution.
4. **DB trigger** on `messages`: status may become `sent` only if `send_compliance_decision_id` points to an `allow` decision ≤ 15 min old and `suppression_checked_at` ≤ 15 min old. Defence in depth against code bugs.
5. **Mode resolution**: `live` iff adapter `mode=live` AND `tenant.live_mode` AND `GLOBAL_GO_LIVE` AND all go-live checklist items signed (DB check). Anything else → `DryRunAdapter`.
6. **Dry-run log**: `dry_run_log` row per would-be effect (effect type, adapter, payload with PII encrypted, idempotency key, created_at). Phase reports quote these as proof of zero real side effects.
7. **Sandbox mode** (provider test accounts) is treated as *not live* for gating but calls the provider's sandbox endpoint; it still requires an explicit env flag per adapter.

## Consequences
+ A single place to audit; impossible to send from a forgotten code path without failing CI and the DB trigger.
− Slightly more ceremony for features like "send test email to myself" — implemented as a dry-run render preview, never a real send.

## Reversibility
The gateway pattern is foundational and should not be reversed.
