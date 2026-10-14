# Phase 7 — Go-live preparation: Report (2026-10-14)

## 1. Summary
Everything technical that can be prepared before go-live is now built. **Going live itself stays the owner's decision and action.** Nothing in this phase switches anything on or signs anything off.

- **Sending-domain checks.** For every sending domain, PipelineOS reads the public DNS and checks the three records mail providers require:
  - **SPF:** exactly one record, including your mail provider, and never `+all`.
  - **DKIM:** Microsoft 365 needs both selectors; Google needs its TXT key.
  - **DMARC:** a valid policy. `p=none` is accepted with a note to tighten it later.

  Each problem is written in plain words, so you can forward it to whoever manages the domain. "Check DNS" runs it on demand from the Inboxes page; it also runs every day by itself, and a record removed later is noticed. A domain passing all three moves from "setup" to "warming". Onboarding already requires a verified domain.
- **Mailbox warm-up.** A new inbox starts at 10 emails a day (in `config/warmup.yaml`) and rises by 2 for every day it actually sent real email, up to 40. After 21 such days with healthy bounce and complaint rates (the kill switch's limits), it becomes "active". Dry-run days don't count, because they warm nothing. A paused or unhealthy inbox never advances.
- **Readiness list.** "Before you switch on" on the Go-live page checks ten items automatically and says who acts on each:
  1. company details
  2. UK rules reviewed by your solicitor
  3. UK enabled
  4. tax rules checked by your accountant
  5. sending domains verified
  6. inboxes warmed up
  7. real providers configured
  8. a pilot client onboarded
  9. the go-live checklist signed
  10. the server switch (`GLOBAL_GO_LIVE`)

  It only reads; the sign-offs stay yours.

## 2. Changes

| Area | What |
|---|---|
| Migration `0011` | `sending_domains.dns_issues`, `dns_checked_at`; `inboxes.warmup_last_day` (the downgrade removes only these recomputable results) |
| DNS | `domain/deliverability/dns_check.py`: SPF, DKIM and DMARC rules per provider, plain-language findings, read-only public DNS |
| Warm-up | `domain/deliverability/warmup.py` + `config/warmup.yaml`; at most one step per inbox per day, counted only on live-send days; promotion audited |
| Daily job | `workflows/daily.py` + Celery `pipelineos.system.daily_deliverability`: DNS re-check of every domain, kill switch, then warm-up. A DNS outage doesn't stop the rest |
| API | `GET /sending-domains`, `POST /sending-domains/{id}/check` (operator only), `GET /governance/readiness` |
| Dashboard | Inboxes: "Sending domains" with SPF/DKIM/DMARC status, findings and "Check DNS". Go-live: "Before you switch on" |

## 3. Tests
- Backend: **580 passed** (Phase 6.4: 570). New:
  - 6 unit tests (DNS rules, warm-up plan)
  - 4 database and security tests: DNS stored and re-checked daily, client accounts refused; warm-up advances only on real-send days and once a day; an unhealthy inbox is never promoted; readiness signs nothing
- Browser: **12 passed** (6 + 6), with accessibility checks, including the new readiness list and sending-domains section.
- Lint, types, import contracts: clean.
- On the owner's PC: included in the Phase 8 run (600 passed, 0 failed).

## 4. What the owner does at go-live (unchanged)
See `docs/GO-LIVE-RUNBOOK.md`. The readiness list now shows progress on that runbook automatically.
