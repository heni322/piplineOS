# Phase 2 — Foundations: Report (2026-10-12)

## 1. Summary
Phase 2 built the safety infrastructure, in the order the brief requires, before anything that can produce outreach. It covers the database (all 33 required tables plus supporting ones, client data separation enforced by PostgreSQL row-level security), per-client PII encryption with masking and logged unmasking, the append-only audit log, versioned legal/tax config, the pure compliance engine, the global suppression list with signed one-click unsubscribe, the lead state machine, and the single outbound gateway in dry-run mode, backed by database triggers. It also includes authentication with operator MFA, the adapter framework with mocks for every service, the validated LLM port, the outbox and Celery wiring, and a Next.js skeleton.

**246 automated tests pass** (238 backend against real PostgreSQL 16 + pgvector, 4 frontend component tests, 4 browser tests including WCAG 2.1 AA checks). Lint and strict type checks are clean. The compliance engine has 100% branch coverage. All required dry-run traces behave as specified, with **zero real side effects**.

Writing the tests surfaced **seven real defects, all fixed**. They are listed in §6.

One item is **not fully met**: one high-severity frontend advisory has no upstream fix (dev-only lint dependency). The Docker Compose stack has **not been run** yet, because there is no Docker engine here or running on your machine. The compose file is validated, and the same migrations and tests ran against an equivalent local PostgreSQL 16.

## 2. Changes made

| Area | Files |
|---|---|
| Scaffold | `README.md`, `.gitignore`, `.gitattributes`, `.editorconfig`, `.pre-commit-config.yaml` (ruff, mypy, eslint, gitleaks), `.env.example`, `docker-compose.yml`, `docker/postgres/01-roles.sql`, `scripts/ci.sh`, `scripts/dev-setup.ps1` |
| Migrations | `backend/migrations/versions/0001_core_tenancy.py`, `0002_config_and_compliance.py`, `0003_domain.py`. All are reversible and were tested up → down → up and partial down/up on fresh databases |
| Core | `app/settings.py`, `core/{logging,errors,crypto,masking,security,audit,idempotency}.py`, `db/{session,ddl}.py`, `main.py` |
| Compliance | `domain/compliance/{engine,jurisdiction,subscriber,repository,gate}.py`, `config_sync.py`, `config/**` (14 countries, tax, transfers, go-live checklist, footer, LLM prices) |
| Suppression | `domain/suppression/{service,unsubscribe}.py`, public `api/public.py` |
| State machine | `domain/leads/state_machine.py` |
| Outbound | `domain/outbound/{gateway,modes,dry_run,footer,messages}.py`, `domain/deliverability/hours.py`, `.importlinter` |
| Adapters | `adapters/{email,calendar,crm,enrichment,verifier,signals,companies_house}/` (interface + mock; live stubs raise "not configured") |
| LLM | `llm/{client,backends,prompts,recorder,pricing}.py`, `prompts/common/untrusted_data_policy.v1.md`, `prompts/LOCK.json` |
| Workflows | `workflows/{celery_app,outbox}.py` |
| API | `/api/v1/health`, `/auth/{login,logout,me}`, `/leads` (masked, cursor-paginated), `/leads/{id}/unmask`, `/compliance/decisions`, `/golive`, `/golive/{id}/sign-off`, `/governance/adequacy`, public `/u/{token}` |
| CLI | `pipelineos keys generate`, `config sync`, `users create-operator`, `demo traces` |
| Frontend | Next.js 15.5.27 App Router: login (with TOTP), operator and client shells with empty/loading/error states, public privacy notice, robots.txt, `noindex` on dashboards, i18n catalogue `messages/en-GB.json` |
| Docs | `docs/adr/0009-phase-2-implementation-choices.md`, data model note, this report, `docs/traces-phase-2.json` |

**New dependencies.** All are free and open source, listed in `backend/pyproject.toml` / `frontend/package.json` and locked by `uv.lock` / `pnpm-lock.yaml`. Licences and justifications are in `docs/07-dependencies.md`. No paid services were used. No real API was called.

## 3. Decisions and assumptions
They are recorded in **ADR-0009**. The ones you may want to review:
- Synchronous SQLAlchemy Core with hand-written SQL migrations, instead of the async ORM in the Phase 1 text. Reversible.
- The business window is 09:00–17:00 in the recipient's local time, Mon–Fri, excluding that nation's or country's bank holidays. For a UK prospect whose nation is unknown, every UK nation's holidays are avoided. Tenant-configurable in Phase 3.
- `risky` email verification is blocked, and tenants cannot override it.
- Germany and Austria (consent required) route to **human-executed alternative-channel tasks** by default, as the rollout plan says. Every other country that isn't enabled is blocked, including the UK until you enable it.
- One-click unsubscribe suppresses **globally**, as the brief requires. Whether a domain is suppressed is left to an operator action.
- Login rate limiting runs in-process for now. A Valkey-backed limiter arrives in Phase 6, when more than one API instance runs.
- The 0001 and 0003 migrations were corrected in place during this phase. They had never been committed or applied outside throwaway test databases. From now on, migrations are append-only.

## 4. Requirements traceability (Phase 2 scope)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 2.1 | Repo structure, pre-commit, README | Met | Repository root; `.pre-commit-config.yaml` |
| 2.2 | Docker Compose (Postgres 5433 + pgvector, Valkey, Mailpit), `.env.example` | Partially met | `docker compose config` validates, but the stack has **not been run** (no Docker engine available) |
| 2.3 | FastAPI skeleton, problem+json errors, JSON logs with correlation IDs, PII scrubber | Met | `tests/security/test_api.py::test_errors_use_problem_json`, `tests/unit/test_logging_settings_masking.py::test_logs_never_contain_emails_or_secrets` |
| 2.4 | DB core, Alembic, roles, fail-closed RLS context | Met | `test_rls.py::test_missing_context_fails_closed`, `test_infra.py::test_migrations_apply_and_roll_back_on_a_fresh_database` |
| 2.5 | Envelope encryption, blind index, suppression hash, masking | Met | `test_crypto_db.py` (wrong tenant/purpose, rotation, key destruction, wrong master key) |
| 2.6 | Tenancy tables with RLS; cross-tenant reads fail | Met | `test_rls.py`: 19 tests (select, update, delete, insert, re-parenting, pooled connections, no BYPASSRLS) |
| 2.7 | Auth: Argon2id, sessions, CSRF, operator TOTP, permission matrix | Met | `test_security.py`, `test_api.py` (MFA required, generic errors, lockout, CSRF, logout revokes) |
| 2.8 | Append-only audit log, PII access log, unmask endpoint | Met | `test_append_only_and_config.py` (even the owner can't rewrite audit), `test_api.py::test_unmask_is_logged_before_data_is_returned` |
| 2.9 | Versioned config sync; everything unverified; countries disabled; adequacy expiry | Met | `test_config_files.py`, `test_append_only_and_config.py`, `/governance/adequacy` (warns ≤ 365 days before 2031-12-27) |
| 2.10 | All 33 required tables, FK indexes, uniqueness (no duplicate prospects or sends) | Met | Migrations 0001–0003; `messages_one_send_per_step`, `prospects_tenant_email`, `leads_campaign_prospect` |
| 2.11 | Compliance engine: every mode, unknown country, stricter rule, sole traders, IE vs NI | Met | `test_compliance_engine.py` (100% branch coverage), `test_jurisdiction.py`, `test_subscriber.py` |
| 2.12 | Suppression (hash-only, global), signed and tamper-proof unsubscribe | Met | `test_tokens_footer_modes.py`, `test_api.py::test_one_click_unsubscribe_flow`, `test_rls.py::test_suppression_list_is_the_only_cross_tenant_data` |
| 2.13 | State machine with audit and optimistic locking; invariants | Met | `test_state_machine_table.py`, `property/test_state_machine_props.py` (500 random walks) |
| 2.14 | Idempotency keys, outbox, Celery app and beat | Met (Celery worker not run live) | `test_infra.py::test_idempotent_replay_and_conflict`, `test_outbox_relays_once_and_retries_failures` |
| 2.15 | Outbound gateway, dry-run, DB send trigger, import contract, three-key switch | Met | `test_gateway.py` (13 tests), `test_send_gate_bypass.py` (10 tests), `test_import_contracts.py`, `test_tokens_footer_modes.py::test_three_key_switch` |
| 2.16 | Adapter interfaces + mocks for every service; rate limiter | Met | `app/adapters/**`; live implementations deliberately raise until Phase 6 |
| 2.17 | LLM port: validation, retry with feedback (max 2), escalation, refusal, timeout, budget, cost logging, prompt lock | Met | `tests/contract/test_llm_client.py` (12 tests), `test_infra.py::test_llm_calls_are_recorded_with_prompt_version` |
| 2.18 | Frontend skeleton: i18n, noindex dashboards, public privacy page, a11y | Met | vitest 4/4; Playwright 4/4 with axe WCAG 2.1 AA (0 violations) |
| 2.19 | CI script: lint, types, tests, audits, secret scan | Met | `scripts/ci.sh`. Every step was run individually here; gitleaks wasn't installed here and runs via pre-commit |
| 2.20 | Phase report + sample-lead traces | Met | This file; `docs/traces-phase-2.json` |
| DoD | No high/critical vulnerabilities | **Partially met** | Backend: `pip-audit` reports none. Frontend: 0 critical, **1 high with no upstream fix** (see R12) |

**Brief-level edge cases covered so far:** UK sole trader blocked, Ireland vs Northern Ireland, UK company with a German prospect (stricter rule), unknown country, UK nations' different bank holidays and DST changes, invalid/refused LLM output, provider rate limit (no duplicate send), a worker crash mid-send (never resent), and an operator unmask logged with country (e.g. TN). The rest are scheduled for Phases 3–6.

## 5. Test results

| Suite | Command | Result |
|---|---|---|
| Backend (unit, property, contract, integration, security) | `cd backend && uv run pytest` | **238 passed**, 0 failed (≈38 s, real PostgreSQL 16.15 + pgvector 0.6) |
| Compliance engine branch coverage | `pytest tests/unit/test_compliance_engine.py --cov=app.domain.compliance.engine --cov-branch` | **100%** |
| Backend line coverage overall | `pytest --cov=app` | 85% |
| Lint / format / types | `ruff check`, `ruff format --check`, `mypy --strict` | Clean (80 source files) |
| Import contracts | `lint-imports` | 2 kept, 0 broken |
| Prompt lock | `check_lock()` | Clean |
| Backend dependencies | `pip-audit` | No known vulnerabilities |
| Frontend | `pnpm lint && pnpm typecheck && pnpm test && pnpm build` | Clean; vitest **4 passed** |
| Browser E2E + accessibility | `pnpm e2e` | **4 passed**; axe WCAG 2.1 AA: 0 violations |
| Frontend dependencies | `pnpm audit` | 0 critical, 1 high (no fix exists), 0 moderate |
| **Baseline comparison** | Phase 0/1: 0 tests | **0 → 246**; no previously passing test fails |

### Dry-run traces (`pipelineos demo traces`, fresh dev database, synthetic data)

| Lead | Queue-time decision | Send | Final state | Dry-run log | Alt. tasks |
|---|---|---|---|---|---|
| 1. UK corporate (Ltd, verified, relevant role) | allow `[OK]` | `dry_run_sent` (mode `dry_run`) | contacted | 1 | 0 |
| 2. UK sole trader | alternative_channel `[INDIVIDUAL_SUBSCRIBER]` | not attempted | alternative_channel | 0 | 1 |
| 3. German prospect at UK company | alternative_channel `[CONSENT_REQUIRED]` (DE stricter than GB) | not attempted | alternative_channel | 0 | 1 |
| 4. Globally suppressed person | block `[SUPPRESSED_GLOBAL]` | not attempted | suppressed | 0 | 0 |
| 5. Suppressed **after** approval | allow `[OK]` | **blocked at send** `[SUPPRESSED_GLOBAL]` | suppressed | 0 | 0 |

**Proof of no real side effects** (database query after the run): `sent` (live/sandbox) = **0**, `dry_run_sent` = 1, `blocked_at_send` = 1, messages in any non-dry-run mode = **0**, dry-run log rows = 1. The database trigger would also have rejected any live send, because the go-live checklist is unsigned (`test_live_send_rejected_by_database_when_checklist_unsigned`).

## 6. Defects found by the tests during this phase (all fixed, with regression tests)

| # | Defect | Impact if shipped | Fix |
|---|---|---|---|
| D1 | YAML read Norway's code `NO` as boolean `false` | Norway config silently broken | Country codes quoted in every config file |
| D2 | An unsubscribe token with altered unused base64 bits still verified | Not a forgery, but "tamper-proof" was not strictly true | Only the canonical encoding is accepted |
| D3 | Background jobs acting inside one tenant could list **all** tenants | Tenant metadata leak | RLS: the scheduler sees all tenants only with no tenant context |
| D4 | Failed logins were rolled back with the error response | **Accounts never locked**; failed-login audit entries lost | Failure bookkeeping is committed before rejecting |
| D5 | Public unsubscribe couldn't resolve its token (FORCE RLS binds the owner too) | **Unsubscribe links would not have worked** | Owner-only SELECT policy for the definer lookup |
| D6 | Engine ignored a country's *corporate* subscriber rule | A stricter rule for corporates could be bypassed | The subscriber rule always applies |
| D7 | Daily caps mixed the gateway clock with the database clock | Caps could under-count | The gateway timestamps sends with its own clock |

## 7. Risks and open issues

| # | Risk | Severity | Mitigation / status |
|---|---|---|---|
| R1 | Docker engine not running on your PC; Compose stack never executed | High (blocks local run) | Start Docker Desktop, then run `scripts/dev-setup.ps1`; I'll verify end-to-end on your machine |
| R4 | Global cross-tenant suppression may make you a controller of that list | High (legal) | Hash-only, contact-prevention only; **solicitor review** |
| R5 | Named-rep sending with AI-written replies | High (legal/trust) | Disclosure config arrives in Phase 4; solicitor review |
| R12 | `braces` ≤ 3.0.3 (CVE-2026-93687, ReDoS), via `eslint-config-next`; **no patched release exists** | Low in practice (dev-time lint only, never shipped, no untrusted input), but formally "high" | **Needs your acceptance**; recheck at each phase; CI fails only on critical until a fix exists |
| R13 | Next.js 15.5.27 is 12 days old; Next 16 not evaluated | Low | Patch-line upgrade only; evaluate 16 in Phase 6 |
| R14 | Login limiter is per-process | Medium (with several API instances) | Valkey-backed limiter in Phase 6 |
| R15 | LLM prices are `null` in config, so costs are recorded as NULL | Low (cost metric incomplete) | Fill `config/llm_prices.yaml` from Anthropic's pricing page before Phase 3 evals |
| R16 | Footer and privacy notice wording are placeholders | High before go-live | Solicitor review (already a go-live checklist item) |
| R17 | The UK VAT threshold (£90,000) is from my training data | Medium | Marked "verify"; accountant sign-off item |

## 8. What you must do
1. **Start Docker Desktop**, then tell me. I'll run the stack on your machine and confirm it works there (migrations, config sync, traces, API, web).
2. **Accept or reject R12** (dev-only `braces` advisory with no fix available).
3. Optional, before Phase 3 evals: an Anthropic API key (with a monthly cap). Without it, Phase 3 runs on recorded/mock LLM responses.
4. Unchanged and still needed eventually: company details for the footer, plus solicitor/accountant reviews (go-live checklist).

## 9. Next phase plan — Phase 3: MVP vertical slice (UK only), pending your approval
Branch `phase/3-vertical-slice`. One tenant, one niche (UK IT MSPs), English, mocked providers, dry-run only:
1. Realistic synthetic demo dataset (fictional companies, `.example` domains), plus a demo tenant with the UK enabled **in the demo database only**.
2. **Agent 1 (Trigger Hunter):** schema → mock sources (Contracts Finder, Find a Tender, jobs, funding) → `score.v1` prompt → dedupe window → tests.
3. **Agent 2 (Decision-Maker Finder):** schema → mock enrichment, verifier and Companies House → subscriber classification → per-field sources → tests.
4. Compliance gate wired into the pipeline (already built), with alternative-channel tasks.
5. **Agent 3 (Personalization Writer):** `draft.v1` + `critique.v1` → citation-by-source-ID validator → deterministic footer → self-critique loop (≥ 8/10, ≤ 2 rewrites) → golden eval set (no unsourced claims, footer present, ≤ 90-word opener, UK spelling).
6. **Approval queue** API + UI: email, each claim with its clickable source, compliance decision, quality score, approve/edit/reject in under 30 s, every edit logged.
7. Operator pipeline view, compliance log and dry-run log views.
8. End-to-end integration test signal → contacted (dry-run), a demo script for prospective clients, and the Phase 3 report with traces.
