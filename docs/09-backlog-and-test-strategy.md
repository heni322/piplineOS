# 09 — Phase 2–3 Backlog & Test Strategy

Status: **Proposed (Phase 1)** · Each item = one reviewable increment (one or a few commits) on its phase branch. Order follows the brief: safety infrastructure **before** anything that can cause outreach.

## Phase 2 — Foundations (branch `phase/2-foundations`)

Precondition: Docker Desktop running (Phase 0 risk R1).

| # | Increment | Done when |
|---|---|---|
| 2.1 | Repo scaffold: git init, `.gitignore`, `.gitattributes`, `.editorconfig`, pre-commit (ruff, mypy, eslint, gitleaks), README | `pre-commit run --all-files` passes |
| 2.2 | `docker-compose.yml` (postgres:5433 + pgvector, valkey, mailpit), `.env.example`, settings module | `docker compose up` healthy; settings tests pass |
| 2.3 | Backend skeleton: FastAPI factory, `/api/v1/health`, problem+json errors, structlog JSON logging + correlation IDs + PII scrubber | Unit tests incl. "no email in logs" test |
| 2.4 | DB core: SQLAlchemy async, Alembic, DB roles, RLS session hook (fail-closed), testcontainers fixture | Migration up/down on fresh DB |
| 2.5 | Crypto & masking: envelope encryption, blind index, suppression hash, masking serialisers | Unit tests, key-rotation test |
| 2.6 | Schema migration 1: tenancy & identity (tenants, users, sessions, tenant_configs, niches, campaigns) + RLS policies | Isolation tests pass |
| 2.7 | Auth: Argon2id, sessions, CSRF, TOTP for operators, role/permission matrix, invite CLI | Auth + permission matrix tests |
| 2.8 | Audit log (partitioned, append-only) + `pii_access_log` + unmask endpoint | Append-only enforced (UPDATE/DELETE denied test) |
| 2.9 | Config system: YAML schemas, `config sync`, versioned tables (compliance_rules, country_rollout_status, tax_rules, tax_settings, transfer_mechanisms, sub_processors, golive_checklist_items) + seeds (all unverified, all countries disabled) | Sync idempotent; version-on-change test; adequacy warning job test |
| 2.10 | Schema migration 2: remaining tables (companies … llm_calls, outbox, idempotency_keys, scheduled_actions, dry_run_log, unsubscribe_tokens) with FK indexes + unique constraints | Fresh DB up/down; constraint tests |
| 2.11 | **Compliance engine** (pure) + jurisdiction parser (IE vs GB-NIR) + subscriber-type rules | 100% branch coverage; all brief unit cases |
| 2.12 | **Suppression service** (global/tenant, hash-only) + signed unsubscribe tokens + public one-click endpoint | Tampered-link tests |
| 2.13 | **State machine** core + transition audit + optimistic locking | Hypothesis invariant tests |
| 2.14 | Idempotency (HTTP middleware + job helper) + outbox relay + Celery app + beat | Duplicate-request tests |
| 2.15 | **Outbound gateway + DryRunAdapter + DB send trigger + import-linter contract + three-key mode resolution** | Bypass tests (incl. raw SQL as app role) |
| 2.16 | Adapter framework: base interfaces + mock implementations for all services; rate limiter | Contract tests |
| 2.17 | LLM client port: mock + recorded + Anthropic impl (not called without key), validation/retry/escalate, cost logging, budgets, prompt LOCK check | Contract tests incl. invalid JSON/refusal |
| 2.18 | Frontend skeleton: Next.js, i18n, auth pages, layout, `noindex` on dashboards, public privacy page shell, a11y lint, Playwright smoke | `tsc`, eslint, vitest, playwright smoke pass |
| 2.19 | CI script (`make ci`): lint, types, tests, pip-audit, pnpm audit, gitleaks | Green locally |
| 2.20 | Phase 2 report + 4-lead compliance trace (engine-level) | Report delivered |

## Phase 3 — MVP vertical slice, UK only (branch `phase/3-vertical-slice`)

| # | Increment |
|---|---|
| 3.1 | Demo seed: one tenant (*"Northwind IT Services Ltd"*, fictional), niche "UK IT MSPs", personas, LIA/DPA marked present **for demo tenant only**, GB enabled **in the demo DB only** |
| 3.2 | Agent 1: schema → mock adapters (Contracts Finder, Find a Tender, jobs, funding) with realistic synthetic data → `score.v1` prompt → logic (dedupe window) → tests |
| 3.3 | Agent 2: schema → mock enrichment + verifier + Companies House mock → subscriber-type classification → field sources → tests |
| 3.4 | Compliance gate wired into the pipeline (queue time) + alternative-channel tasks |
| 3.5 | Agent 3: schema → `draft.v1` + `critique.v1` → source-ID citation validator → deterministic footer → self-critique loop (≥ 8/10, ≤ 2 rewrites) → tests + eval set |
| 3.6 | Approval queue API + UI (email, claims with clickable sources, compliance decision, quality score; approve/edit/reject; < 30 s target; keyboard shortcuts) |
| 3.7 | Gateway dry-run send with caps, local business hours, UK bank holidays per nation, deliverability gates (mock inbox health) |
| 3.8 | Operator pipeline view (by state, paginated) + compliance log + dry-run log view |
| 3.9 | Integration test: signal → contacted (dry-run) for the 4 required sample leads |
| 3.10 | Demo script + recorded walkthrough data; Phase 3 report |

## Test strategy

### Layers

| Layer | Tooling | Scope | Gate |
|---|---|---|---|
| Unit | pytest, Hypothesis | Compliance engine (every mode, unknown country, stricter-rule, sole trader, IE vs NI), suppression, state machine, qualification, billing eligibility, pricing combos, tax config, idempotency, kill-switches, holidays/DST | Must pass; compliance engine 100% branch coverage |
| Property | Hypothesis | State-machine invariants (doc 04 §6), idempotency under random retries | Must pass |
| Contract | pytest + Pydantic | Every agent I/O schema; invalid LLM JSON, refusal, unsourced claim, unknown source ID; every adapter against its interface | Must pass |
| Integration | testcontainers (real Postgres 16 + Valkey) | RLS, migrations up/down, pipeline flows, outbox replay, worker crash mid-send | Must pass |
| Security | pytest | Cross-tenant access, auth bypass, permission matrix, tampered unsubscribe, gate bypass via raw SQL, SSRF guard, log scrubbing, prompt injection | Must pass |
| LLM evals | `tests/evals/run_evals.py` with recorded/mocked responses (and optional live run with your key, cost-capped) | Agent 3: 0 unsourced claims, footer present, ≤ 90-word opener, language/formality, UK spelling. Agent 4: ≥ 50 labelled replies per language incl. DP requests + injection; recall ≥ 95% on `data_protection_request` and `unsubscribe` | Thresholds enforced in CI (recorded mode) |
| Frontend | vitest + Testing Library + axe | Components, empty/loading/error states, a11y | Must pass |
| E2E | Playwright | approve → dry-run send → reply → book → confirm → attend → bill (complete by Phase 5) | Must pass |
| Load | Locust (Phase 6) | 10k leads/day, dashboard < 2 s at 100k leads | Report |

### Golden datasets (synthetic, committed)
- `evals/datasets/writer_uk_msp.jsonl` — ≥ 40 prospect/source bundles incl. pages with injected instructions and pages with no usable facts (expected: fewer claims, never invented).
- `evals/datasets/replies_en.jsonl` — ≥ 50 labelled replies (Phase 4), French set at Stage 3.
- `evals/datasets/jurisdiction.jsonl` — location strings → expected country/subdivision.

### Required dry-run traces (every phase report)
1. UK corporate (Ltd, verified email, relevant role) → `allow` → `contacted` (dry-run).
2. UK sole trader → `alternative_channel` (`INDIVIDUAL_SUBSCRIBER`), never emailed.
3. German prospect at UK company → stricter rule → `alternative_channel` (`CONSENT_REQUIRED`).
4. Globally suppressed person → `suppressed` at queue time; also proven blocked at send time if suppressed after approval.

### Baseline
Phase 0 baseline: **0 tests** (greenfield). Every later phase compares against the previous phase's counts.
