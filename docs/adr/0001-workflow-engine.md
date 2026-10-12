# ADR-0001 — Celery + Valkey with a Postgres outbox (not Temporal)

**Status:** Proposed

## Context
The orchestrator needs durable, idempotent, retryable workflows; scheduled jobs (reminders, retention); per-inbox rate limits; and ~10k leads/day. Dev machine has 16 GB RAM and ~31 GB free disk. Budget is lean. The spec allows Temporal or Celery + Redis.

## Decision
- **Celery 5** workers with **Valkey 8** (BSD-3, wire-compatible Redis fork) as broker.
- **Durability lives in Postgres, not the broker**: transactional outbox table, `idempotency_keys` table, unique constraints on sends/bookings, and the lead state machine stored in `leads.state` with optimistic locking (`version` column).
- Tasks are idempotent by construction: each task loads state, checks it is still in the expected state, does its work, and transitions with `UPDATE … WHERE version = :v`.
- `acks_late=True`, `task_reject_on_worker_lost=True`, visibility timeout > longest task.
- Long waits (OOO until a date, reminders 24h/1h before meeting) are rows in `scheduled_actions` polled by beat — not long-lived Celery countdowns, which are lost if the broker restarts.

## Alternatives
- **Temporal**: best-in-class durable execution. Rejected for MVP: extra server + its own DB (~1.5–2 GB RAM locally), steeper learning curve, more ops at deployment. Revisit if workflows grow long-running with complex compensation.
- **Redis (not Valkey)**: Redis 7.4+ is under RSAL/SSPL (8.0 adds AGPL). Valkey avoids licence ambiguity at zero cost.
- **Dramatiq / arq**: smaller ecosystems; Celery beat + flower + mature retry semantics win.

## Consequences
+ Light footprint, well-known, cheap to host.
− We implement workflow durability ourselves (outbox, reconciler). Mitigated by keeping the state machine explicit and fully tested.

## Reversibility
High. Domain logic is broker-agnostic (`app/workflows/` calls an internal `TaskRunner` port); swapping to Temporal changes the runner, not the domain.
