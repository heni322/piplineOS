# 07 — Dependencies, Licences & Costs

Status: **Proposed (Phase 1)** · Rule: open-source first; every paid service justified. Versions are pinned at Phase 2 scaffold time via lockfiles (exact versions recorded in the Phase 2 report).

## 1. Runtime / infrastructure

| Component | Purpose | Licence | Cost | Notes |
|---|---|---|---|---|
| Python 3.12 | Backend language | PSF | Free | Containers pin 3.12; host has 3.13 |
| PostgreSQL 16 (`pgvector/pgvector:pg16`) | Primary DB, RLS, vector search | PostgreSQL / pgvector PostgreSQL licence | Free | Runs on port **5433** locally to avoid your PG 17 |
| Valkey 8 | Celery broker, rate-limit counters, locks | BSD-3 | Free | Chosen over Redis due to Redis licence change |
| Node 22 LTS + pnpm | Frontend toolchain | MIT | Free | Already installed |
| Docker Compose | Local dev | Apache-2.0 | Free (Docker Desktop is free for personal / small business < 250 staff & < $10M revenue) | |
| Mailpit (dev only) | Fake SMTP inbox to *see* dry-run renders | MIT | Free | Receives nothing outside the dev network |

## 2. Backend libraries (Python)

| Library | Purpose | Licence |
|---|---|---|
| fastapi, uvicorn | API | MIT / BSD-3 |
| pydantic v2, pydantic-settings | Validation, settings | MIT |
| sqlalchemy 2 (async) + asyncpg / psycopg 3 | ORM / driver | MIT / Apache-2.0 / LGPL-3 (psycopg; dynamically linked, OK) |
| alembic | Migrations | MIT |
| celery 5 | Task queue | BSD-3 |
| anthropic | LLM SDK | MIT |
| httpx | HTTP client for adapters | BSD-3 |
| tenacity | Retries/backoff | Apache-2.0 |
| cryptography | AES-GCM, HMAC | Apache-2.0 / BSD |
| argon2-cffi, pyotp | Password hashing, TOTP | MIT |
| structlog | JSON logging + correlation IDs | MIT / Apache-2.0 |
| holidays | UK nations + EU bank holidays | MIT |
| pgvector (python) | Vector column type | MIT |
| import-linter | Enforce gateway import contract | BSD-2 |
| faker | Synthetic data | MIT |
| **Dev/test**: pytest, pytest-asyncio, hypothesis, testcontainers, respx, freezegun/time-machine, coverage, ruff, mypy, pip-audit, gitleaks (binary, MIT), locust (Phase 6) | | MIT/Apache/BSD |

## 3. Frontend libraries

| Library | Purpose | Licence |
|---|---|---|
| next 15 (App Router), react 19, typescript | Dashboards + public pages | MIT / Apache-2.0 |
| tailwindcss | Styling | MIT |
| radix-ui primitives (via shadcn/ui copy-in components) | Accessible components (WCAG) | MIT |
| @tanstack/react-query, @tanstack/react-table | Data fetching, paginated tables | MIT |
| zod | Runtime validation of API responses | MIT |
| next-intl | i18n (all strings in catalogues) | MIT |
| openapi-typescript | Types generated from FastAPI OpenAPI | MIT |
| **Dev/test**: eslint (+ jsx-a11y), vitest, @testing-library/react, playwright, @axe-core/playwright | | MIT / Apache-2.0 / MPL-2.0 (axe) |

## 4. External services (all behind adapters; **mock only until Phase 6**)

| Service | Purpose | Cost | Status |
|---|---|---|---|
| Anthropic API | Agents 1, 3, 4 | Pay-per-token; estimate in §5 | Needs your key (Phase 3 can run fully on mocks/recordings) |
| Companies House API | Subscriber-type (legal form), company numbers | **Free** (API key) | Recommended first real integration |
| UK Contracts Finder / Find a Tender, EU TED | Tender signals | **Free** official APIs/feeds | Phase 3 mocks; real in Phase 6 |
| Job-posting / funding signal source | Signals | TBD — free options are limited (e.g. Adzuna API free tier for jobs); paid providers otherwise | Decision before Phase 6 |
| Enrichment (Cognism / Dropcontact / Apollo / other) | Decision-maker + email | Paid; choose one | Decision before Phase 6; must offer UK/EU GDPR-compliant sourcing and DPA |
| Email verification (e.g. a verifier service) | Bounce prevention | ~£0.002–0.01 per check (typical market range, to verify) | Decision before Phase 6 |
| Email sending (Google Workspace / M365 inboxes via API, or Smartlead/Instantly) | Send + receive | Workspace ~£6–7/user/month (verify) | Decision before Phase 6 |
| Cal.com | Booking | Free self-host (AGPL) or free cloud tier | Default |
| Hosting (UK) | Prod | Est. £40–120/month for MVP (small VM + managed Postgres) | Phase 6 comparison |

Prices are indicative ranges from general market knowledge and **must be verified** before any purchase.

## 5. LLM cost model (rough, for planning only)

Per lead that reaches drafting: Agent 1 scoring (~2k in / 300 out), Agent 3 draft + critique + ≤ 2 rewrites (~6–15k in / 2–4k out), Agent 4 per reply (~3k in / 500 out). Exact £ figures depend on current Anthropic pricing, which will be loaded into `config/llm_prices.yaml` from the official pricing page at Phase 3 and tracked per tenant/lead in `llm_calls`. Budget caps per step are configurable and default conservatively.

## 6. Explicitly rejected

| Rejected | Why |
|---|---|
| Temporal | RAM/disk footprint for MVP (ADR-0001) |
| LangGraph | Second state store; not needed (ADR-0002) |
| Redis ≥ 7.4 | Licence (RSAL/SSPL/AGPL) — Valkey instead |
| Auth0 / Clerk | Paid at scale, extra US sub-processor (ADR-0008) |
| Any LinkedIn scraping/automation tool | Prohibited by brief and LinkedIn ToS |
| Open/click tracking pixels | Off by default (deliverability + privacy) |
