# PipelineOS

Compliance-first, multi-tenant, multi-agent outbound system for UK (then European) B2B meeting generation,
run as a done-for-you service. **Everything runs in dry-run mode by default**: no real email, booking, CRM write
or paid API call can happen unless three independent switches are on (adapter mode, tenant live mode, and the
operator-signed go-live checklist with `GLOBAL_GO_LIVE=true` in production).

> Not legal or tax advice. Legal/tax rules are configuration marked `verified_by_counsel: false` /
> `verified_by_accountant: false` until a solicitor / accountant signs them off.

## Status

| Phase | State |
|---|---|
| 0 Investigation | Done |
| 1 Architecture & plan | Done — see `docs/` |
| 2 Foundations | Done |
| 3 MVP vertical slice (UK) | Done — demo in `docs/DEMO.md` |
| 4–10.1 Replies, billing, production readiness, real adapters, countries, replies safety net | Done — `docs/PHASE-*-REPORT.md` |
| 11 Free sources only (`FREE_ONLY`) | **Done, awaiting review** — `docs/PHASE-11-REPORT.md` |
| 12 Stronger free search: market-wide funding, EU awards, insolvency filter, published contacts | **Done, awaiting review** — `docs/PHASE-12-REPORT.md` |
| 13 Contact tasks queue (operator page) | **Done, awaiting review** — `docs/PHASE-13-REPORT.md` |
| 14 Client targeting form (keywords, SIC codes, countries, watchlist) | **Done, awaiting review** — `docs/PHASE-14-REPORT.md` |

The current state, and what is still needed from you, is in `docs/STATUS.md`.

## Quick start (Windows, PowerShell)

Prerequisites: Docker Desktop **running**, [uv](https://docs.astral.sh/uv/), Node 22 + pnpm.

```powershell
./scripts/dev-setup.ps1                     # creates .env with fresh keys, starts Postgres(5433)/Valkey/Mailpit, migrates, syncs config
cd backend; uv run pipelineos demo seed      # demo tenant + operator login (shown once)
uv run pipelineos demo run                  # agents: signals -> contacts -> compliance -> drafts for approval
uv run pipelineos demo traces               # 5 synthetic dry-run compliance traces
uv run pipelineos users create-operator you@yourcompany.example   # prints a TOTP enrolment URI once
uv run uvicorn app.main:app --reload        # API on :8000 (docs at /api/docs in dev)
cd ../frontend; pnpm dev                    # web on :3000
```

Postgres runs on **5433** so it never touches the PostgreSQL 17 service on 5432.

## Layout

```
backend/   FastAPI + Celery, SQL migrations (Alembic), compliance engine, outbound gateway, adapters, LLM port, tests
frontend/  Next.js (App Router, TypeScript) dashboards + public privacy notice
config/    versioned legal/tax/governance YAML (countries, tax, transfers, go-live checklist, footer)
docs/      architecture, ADRs, data model, state machine, compliance spec, threat model, phase reports
scripts/   ci.sh (full local CI), dev-setup.ps1
```

## Checks

```bash
./scripts/ci.sh    # ruff, mypy --strict, import contracts, prompt lock, pytest, pip-audit, eslint, tsc, vitest, build, playwright, pnpm audit
```

## Safety guarantees (enforced in code AND in the database)

- One outbound gateway is the only code allowed to import side-effecting adapters (import-linter contract).
- Compliance + global suppression are re-checked at send time; a database trigger refuses `sent` without a fresh
  send-time `allow` decision, a recent suppression check, a non-suppressed recipient — and, for live mode, a tenant in
  live mode with every go-live item signed off.
- Tenant isolation via PostgreSQL row-level security for a runtime role without `BYPASSRLS` (fails closed).
- PII encrypted per tenant (AES-256-GCM envelope), masked by default; every unmask is logged first.
- At-most-once sending: a crash mid-send goes to human reconciliation, never an automatic resend.
