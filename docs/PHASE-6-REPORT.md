# Phase 6 — Production readiness: Report (2026-10-13)

## 1. Summary
PipelineOS can now run on a real UK server and talk to real providers. **Everything stays in dry-run.** Real sending still needs three keys and your signed go-live checklist.

What's new:
- **Real adapters**, all behind the three-key switch:
  - Microsoft 365: mail with the compliance headers intact, calendar, and real-time inbound replies
  - Companies House (read-only)
- **Client accounts by emailed invite and reset links.** Nobody, including the operator, ever sees a client's password.
- **Deliverability kill switch.** An inbox pauses itself when bounces or complaints get too high.
- **Production deployment.** Includes:
  - Docker Compose stack with automatic TLS
  - encrypted nightly backups with a **tested restore**
  - readiness and metrics endpoints, plus alert rules
  - CI with secret and vulnerability scanning
- **Load test at 100,000 leads.** Worst page p95 is 487 ms (target under 2 s).
- **Runbooks** for deployment, go-live and operations.

Not built, because they wait on your choices: enrichment and email-verification providers, Google Workspace, and payment collection.

## 2. Changes made

| Area | What |
|---|---|
| Adapters | `adapters/http.py` (shared error mapping), `companies_house/live.py`, `msgraph_auth.py`, `email/msgraph.py` (MIME `sendMail`, deterministic Message-ID), `email/sink.py` (sandbox → local sink only), `calendar/msgraph.py` (`transactionId`), `inbound/msgraph.py` (fetch, subscribe, renew) |
| Outbound | `outbound/providers.py` chooses live / sandbox senders from settings; `outbound/system_mail.py` for transactional mail through the gateway |
| Webhook | `POST /webhooks/msgraph`: validation echo, per-mailbox HMAC `clientState`, rejected notifications audited |
| Accounts | Migration `0007` (`account_tokens`, definer functions `auth_set_password` / `auth_issue_reset`); invite, forgot-password and set-password flows; pages `/forgot-password`, `/set-password`; invoice-issued notice to client admins |
| Deliverability | `deliverability/killswitch.py`, `config/deliverability.yaml`; bounces handled before the reply agent; inbox list and resume (with reason) endpoints |
| Rate limiting | `core/ratelimit.py`: Valkey token bucket shared across workers, local fallback; applied to login, reset and the public unsubscribe / data-request pages |
| Ops | `GET /api/v1/ready`, `GET /metrics` (bearer token, aggregates only), `ops/prometheus/alerts.yml` |
| Deployment | `docker-compose.prod.yml`, `deploy/Caddyfile`, `deploy/postgres-init/01-roles.sh`, `deploy/backup.Dockerfile`, `.env.prod.example` |
| Backups | `ops/backupcrypt.py` (AES-256-GCM, scrypt), `scripts/backup.sh`, `backup-loop.sh`, `restore.sh` (new database only), `restore-drill.sh` |
| CI / security | `.github/workflows/ci.yml`, `.github/dependabot.yml`, `.gitleaks.toml` |
| Performance | `billing.meeting_facts_bulk`: `/billing/meetings` 380 ms → 20 ms; `scripts/loadtest/` |
| AI prices | `config/llm_prices.yaml` with Anthropic's list prices (USD) and an FX rate for you to set |
| Dev | `worker-replies` added to the dev compose (D26) |
| Docs | ADR-0014, `DEPLOYMENT.md`, `GO-LIVE-RUNBOOK.md`, `OPERATIONS-RUNBOOK.md`, `loadtest-phase-6.txt`, `traces-phase-6.json`, `demo-run-phase-6.json`, this report |

Commits on `phase/6-production-readiness` (branched from `phase/5-billing-portal` at 328726d): 309a6d6, 030105f, fb72e4b, 896c7e0, 2dc4b22, and the docs commit.

## 3. Decisions (ADR-0014)
- Microsoft 365 first via Graph app-only auth, restricted to the sending mailboxes. Google later.
- Raw MIME so List-Unsubscribe survives; idempotent message and meeting ids.
- Real adapters reachable only through the gateway and the three-key switch; sandbox sends only to a local sink.
- Companies House: exact match or "unknown" (which blocks).
- System email uses the same gate.
- Invite / reset tokens replace one-time passwords.
- Automatic inbox pause on bad deliverability.
- One London VM to start; managed database later.
- Encrypted backups by a read-only backup role; restore never overwrites.
- AI prices in USD with your FX rate.

## 4. Requirements traceability (Phase 6 scope)

| Requirement | Status | Evidence |
|---|---|---|
| Real email / calendar adapters, sandbox until go-live | Met (Microsoft 365) | `tests/contract/test_real_adapters.py` (17); `tests/security/test_real_adapter_switch.py`: live adapter configured but tenant or global key off → dry-run, sandbox → local sink only |
| Unsubscribe headers on real mail | Met | MIME builder test asserts `List-Unsubscribe` and `List-Unsubscribe-Post` |
| No duplicate sends or meetings on retry | Met | Deterministic Message-ID and `transactionId` tests |
| Inbound replies, webhook authenticity | Met | Webhook tests: validation echo, bad `clientState` rejected and audited, no secret → 404 |
| Companies House lookup | Met | Contract tests: exact match, type map, ambiguous → None |
| Enrichment, email verification | Deferred | Waiting on your provider choice (R29) |
| Google Workspace | Deferred | R30 |
| Gate can't be bypassed (incl. system mail, retries) | Met | System mail through the gateway; send-gate trigger unchanged; `test_real_adapter_switch.py` |
| Account security (no shared passwords, single-use tokens, rate limits) | Met | `tests/security/test_accounts.py`: token reuse refused, expiry, sessions revoked, reset doesn't reveal accounts, 3 per hour |
| Kill switch, alerts, reply SLA | Met | `tests/unit/test_killswitch_ratelimit.py`; `tests/security/test_ops.py`; alert rules file |
| Metrics without PII | Met | `test_ops.py`: token required, output has counts only |
| UK hosting, TLS, secrets | Met (ready to deploy) | `docker-compose.prod.yml`, Caddy, Docker secrets; `DEPLOYMENT.md` |
| Backups with tested restore | Met | `tests/unit/test_backupcrypt.py`; restore drill (§5.3) |
| Load: 100k leads, pages under 2 s | Met | `docs/loadtest-phase-6.txt` (§5.2) |
| CI, secret scan, no new high/critical vulnerabilities | Met locally; Trivy left to CI (R33) | gitleaks: no leaks; pip-audit and pnpm prod audit clean |
| Go-live checklist never self-signed | Met | Unchanged; `GO-LIVE-RUNBOOK.md` lists your steps |
| Migrations reversible | Met | 0007 up → down → up |

## 5. Test results

| Suite | Result |
|---|---|
| Backend `uv run pytest` | **495 passed**, 0 failed (real PostgreSQL 16 + pgvector). Breakdown: <ul><li>unit 292</li><li>property 1</li><li>contract 30</li><li>integration 109</li><li>security 58</li><li>evals 5</li></ul> |
| Frontend components | **12 passed** |
| Browser (2 stages, fresh databases) | **9 passed** (6 + 3), axe: 0 violations, including the new account pages |
| Lint / types / import contracts / prompt lock | Clean (ruff, mypy --strict, eslint, tsc); 2 contracts kept |
| Migrations | 0007 up → 0006 → up: OK |
| Dependency audits | Backend: none. Frontend production: none |
| Secret scan | gitleaks: no leaks in the full history or the working tree |
| Backend on **your Windows PC** | **495 passed, 0 failed, 0 skipped** (§5.4) |
| **Baseline** | Phase 5: 464 → Phase 6: **516**. No previously passing test fails |

### 5.1 Demo and the required sample-lead traces (fresh database)
**Traces** (`docs/traces-phase-6.json`): every decision identical to Phase 5. Only the database ids of the rule rows differ.
- UK corporate → dry-run sent.
- Sole trader → alternative channel.
- German prospect → alternative channel (consent required).
- Suppressed → blocked.
- Suppressed after approval → blocked at send.

**Demo** (`demo seed` → `run` → `conversations` → `billing`, `docs/demo-run-phase-6.json`): the same October draft as Phase 5.
- Set-up £250, retainer £500, Contoso meeting £300, total **£1,050.00**.
- It still can't be issued: go-live not signed off, supplier details incomplete, tax rule not verified by an accountant.

**No external side effects:**
- the dry-run log holds 10 email sends and 1 calendar event
- 0 messages sent for real (every send recorded `dry_run`)
- 1 invoice, a draft; the invoice counter was never used
- all 32 AI calls were `simulated`
- no Microsoft, Companies House or other provider was called: no keys are configured, and the tests use recorded fake transports

The Phase 5 report said 11 email sends. Running the Phase 5 code on a fresh database today also gives 10, so the count depends on the run date (follow-ups are scheduled relative to today). It is not a change in behaviour.

### 5.2 Load test (100,000 leads, 2-CPU sandbox)
- Single user, all 18 operator and client pages: worst p95 **487 ms** (compliance log).
- 2 API workers, 3 concurrent users: p95 530 ms, 0 errors.
- 2 API workers, 10 concurrent users: p95 1,621 ms, 0 errors. Still under 2 s, but this is the limit of this sandbox. The recommended 4-vCPU server has more headroom.
- Fixed on the way: billing eligibility made one query per meeting (D27), 19× faster now.

### 5.3 Restore drill
Backup → encrypt → restore into a new database → compare: **52 of 52 tables** matched row counts. All 17 encrypted prospect emails decrypted correctly from the restored copy.

### 5.4 On your Windows PC
Synced as branch `phase/6-production-readiness` (git bundle, no history rewritten). Your development database was migrated to `0007` (nothing deleted).
- Docker Desktop wasn't running, so I started it and the dev `postgres` and `valkey` containers.
- Backend against your Docker PostgreSQL: **495 passed, 0 failed, 0 skipped** (≈367 s, JUnit report in `.sync\p6-junit.xml`).

## 6. Defects found during this phase (all fixed, with tests)

| # | Defect | Impact if shipped | Fix |
|---|---|---|---|
| D26 | The dev compose had no worker for the `replies` queue | Replies would wait forever in dev; SLA alerts | `worker-replies` added (prod compose has it too) |
| D27 | Billing eligibility queried the database once per meeting | Billing pages slow at volume (380 ms already at demo size) | Bulk query; 20 ms |
| D28 | The deliverability kill switch was planned in Phase 0 but never built | A bad list could burn a client's sending domain | Built, with tests and an alert |
| D29 | (Phase 5) The operator saw a client's one-time password | A shared secret; weak audit trail | Single-use emailed invite links; no password ever shown |
| D30 | Graph's JSON `sendMail` can't carry List-Unsubscribe | Real mail would have lost the one-click unsubscribe | Raw MIME send |
| D31 | The readiness check couldn't read the schema version as the app role | Server reported "database unreachable" | Grant in 0007; checks split |

Also found: `pg_dump` returns empty tables under forced row-level security. Backups therefore use a dedicated read-only role with BYPASSRLS.

## 7. Risks and open issues

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R29 | Enrichment and email-verification providers not chosen; adapters not built | High before go-live | You choose (licensed UK/EU data, DPA, no LinkedIn scraping); adapter ≈ 1 day each |
| R30 | Google Workspace mail/calendar not built | Medium (only if a client sends from Google) | Same interface as M365; build when needed |
| R31 | AI cost shows "not configured" until you set the USD→GBP rate | Low | Set `fx_usd_gbp` in `config/llm_prices.yaml` |
| R32 | CI actions not pinned to commit SHAs | Low | Pin once the repo is on GitHub (noted in `DEPLOYMENT.md`) |
| R33 | Trivy image scan not run locally (download blocked here) | Low | Runs in CI on the first push |
| R34 | Load measured on a 2-CPU sandbox; production hardware untested | Low | Re-run `scripts/loadtest` on the real server before go-live |
| R35 | A single VM is a single point of failure | Medium | Off-site encrypted backups and a tested restore; move to managed PostgreSQL as clients grow |
| R25–R28, R18 | Pricing, VAT, post-erasure evidence, payments, real-model quality | As before | Unchanged |

## 8. What you must do
1. **Approve Phase 7** (go-live preparation), or ask for changes.
2. **Choose hosting.** The recommendation is option A, one London VM at about $44–48 a month (`DEPLOYMENT.md` §1).
3. **Choose an enrichment provider and an email verifier.** Tell me which and I'll build the adapters.
4. **Set the USD→GBP rate** for AI cost reporting.
5. **Microsoft 365:** create the app registration and the application access policy (`GO-LIVE-RUNBOOK.md` §A.6). Put the IDs in `.env.prod` on the server, never in chat or the repository.
6. **Solicitor and accountant** items, as listed in `GO-LIVE-RUNBOOK.md` §A (unchanged from earlier phases), plus the ICO fee.

## 9. Next phase plan — Phase 7: go-live preparation (pending your approval)
1. Build the enrichment and verifier adapters for the providers you choose.
2. Deploy to staging on your chosen host, run the restore drill and load test there, and wire monitoring.
3. Warm up the sending domains (3–4 weeks, low volume, sandbox → your own addresses).
4. Run the go-live checklist with you. **You sign off each item; I never do.**
5. First pilot client: one approved email to an address you control, then a watched first week (`GO-LIVE-RUNBOOK.md` §C).

Going live is your decision and your action. Nothing in this phase, or the next, switches it on by itself.
