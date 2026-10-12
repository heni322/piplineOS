# 08 — Repository Layout & Conventions

Status: **Proposed (Phase 1)** · Monorepo, local git, `main` + one feature branch per phase (`phase/1-architecture`, `phase/2-foundations`, …). No remote until you provide one.

```
pipelineOS/
├── README.md
├── .env.example                 # every variable documented; no real values
├── .gitignore  .gitattributes  .editorconfig
├── .pre-commit-config.yaml      # ruff, mypy, eslint, prettier, gitleaks
├── docker-compose.yml           # postgres:5433, valkey, mailpit, api, worker-*, beat, web
├── Makefile / justfile          # make up, test, lint, migrate, seed-demo (PowerShell-friendly wrappers in scripts/)
├── config/                      # versioned business/legal data (ADR-0007)
│   ├── countries/GB.yaml IE.yaml DE.yaml …
│   ├── tax_rules.yaml  transfers.yaml  llm_prices.yaml
│   ├── footers/en-GB.yaml (fr-FR.yaml at Stage 3)
│   └── golive_checklist.yaml
├── backend/
│   ├── pyproject.toml  uv.lock  alembic.ini  .importlinter
│   ├── prompts/
│   │   ├── LOCK.json
│   │   ├── trigger_hunter/score.v1.md
│   │   ├── writer/draft.v1.md  writer/critique.v1.md
│   │   └── reply_handler/classify.v1.md  qualify.v1.md  respond.v1.md
│   ├── migrations/versions/
│   ├── app/
│   │   ├── main.py              # FastAPI app factory
│   │   ├── settings.py
│   │   ├── api/v1/              # routers: auth, tenants, leads, approvals, replies, meetings, billing, compliance, dpr, public (unsubscribe, privacy)
│   │   ├── core/                # db session + RLS context, logging, errors, idempotency, security, masking, crypto
│   │   ├── domain/
│   │   │   ├── compliance/      # engine.py (pure), rules loader, jurisdiction.py
│   │   │   ├── suppression/
│   │   │   ├── leads/           # state_machine.py
│   │   │   ├── outbound/        # gateway.py (ONLY side-effect entry), dry_run.py
│   │   │   ├── deliverability/  # caps, hours, kill-switch, warm-up
│   │   │   ├── qualification/  billing/  meetings/  dpr/  retention/
│   │   ├── agents/
│   │   │   ├── base.py          # validate → retry-with-feedback → escalate
│   │   │   ├── trigger_hunter/  decision_maker/  writer/  reply_handler/
│   │   ├── llm/                 # client port, anthropic impl, mock, recorded, cost
│   │   ├── adapters/            # one package per external service
│   │   │   └── <service>/{base.py, mock.py, sandbox.py, live.py}
│   │   ├── workflows/           # celery app, tasks, outbox relay, beat schedule
│   │   └── cli.py               # config sync, seed-demo, create-operator
│   └── tests/
│       ├── unit/  contract/  integration/  security/  property/
│       └── evals/ { datasets/, golden/, run_evals.py }
├── frontend/
│   ├── package.json  pnpm-lock.yaml  next.config.ts  tsconfig.json
│   ├── messages/en-GB.json      # i18n catalogues
│   ├── src/app/(operator)/…  (client)/…  (public)/privacy/[locale]/  unsubscribe/
│   ├── src/components/  src/lib/api (generated types)  src/lib/zod
│   └── tests/ (vitest)  e2e/ (playwright)
├── docs/                        # this documentation set
└── scripts/                     # dev helpers (ps1 + sh)
```

## Conventions

- **Commits**: Conventional Commits (`feat(compliance): …`), one logical change each.
- **Lint/type gates**: `ruff check`, `ruff format --check`, `mypy --strict` (backend app code), `tsc --noEmit`, `eslint` — all must pass in CI and pre-commit.
- **Errors**: RFC 9457 `application/problem+json` with `type, title, status, detail, instance, correlation_id, errors[]`.
- **Pagination**: cursor-based (`?cursor=&limit=`, max 200) for all list endpoints.
- **Idempotency**: `Idempotency-Key` header required on every POST with external side effects; stored response replayed.
- **Correlation IDs**: `X-Correlation-ID` accepted/generated; propagated to Celery tasks and logs; per-lead correlation stored on `leads`.
- **Windows dev**: everything runs in containers; line endings normalised via `.gitattributes` (LF in repo).
