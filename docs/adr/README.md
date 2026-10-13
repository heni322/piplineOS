# Architecture Decision Records

Format: Context → Decision → Alternatives → Consequences → Reversibility.
Status values: Proposed · Accepted · Superseded. All are **Proposed** until the Phase 1 plan is approved.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-workflow-engine.md) | Celery + Valkey with Postgres outbox instead of Temporal | Proposed |
| [0002](0002-agent-framework.md) | Thin Anthropic SDK wrapper + deterministic state machine instead of LangGraph | Proposed |
| [0003](0003-tenant-isolation.md) | Postgres Row-Level Security for tenant isolation | Proposed |
| [0004](0004-dry-run-and-outbound-gateway.md) | Single outbound gateway, three-key live switch | Proposed |
| [0005](0005-pii-encryption-and-masking.md) | Application-level PII encryption, blind indexes, masking by default | Proposed |
| [0006](0006-prompt-versioning-and-llm-validation.md) | File-based versioned prompts, Pydantic-validated outputs | Proposed |
| [0007](0007-config-as-versioned-data.md) | Legal/tax/country rules as versioned YAML → immutable DB rows | Proposed |
| [0008](0008-auth.md) | First-party auth with Argon2id + TOTP MFA, server-side sessions | Proposed |
| [0009](0009-phase-2-implementation-choices.md) | Phase 2 implementation choices and deviations | Proposed |
| [0010](0010-phase-3-agents-simulator-approval.md) | Phase 3 agents, LLM simulator, approval desk | Proposed |
| [0011](0011-delegated-decisions-2026-10-12.md) | Decisions taken under delegated authority | Accepted |
| [0012](0012-phase-4-replies-booking.md) | Phase 4 reply handling, data-protection requests, booking and show rate | Proposed |
| [0013](0013-phase-5-billing-portal.md) | Phase 5 billing, client sign-offs, portal and reporting | Proposed |
| [0014](0014-phase-6-production-readiness.md) | Phase 6 real adapters, accounts, deliverability, hosting, backups | Proposed |
| [0015](0015-live-data-and-client-crm.md) | Real data sources and the client's CRM (Phases 6.2–6.3) | Proposed |
