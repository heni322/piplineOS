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
