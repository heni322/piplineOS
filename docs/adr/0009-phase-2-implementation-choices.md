# ADR-0009 — Phase 2 implementation choices

**Status:** Proposed (recorded at end of Phase 2)

| Decision | Instead of (Phase 1 text) | Why | Reversible |
|---|---|---|---|
| Synchronous SQLAlchemy 2 Core + `psycopg` 3, parameterised `text()` SQL | Async SQLAlchemy ORM | Celery workers are synchronous; RLS context per transaction is simpler and testable; FastAPI runs sync endpoints in a thread pool. Measured in Phase 6 load test. | Yes |
| Hand-written SQL migrations (Alembic `op.execute`), no ORM models / autogenerate | ORM + autogenerate | RLS policies, triggers, SECURITY DEFINER functions and partial indexes are SQL anyway; one source of truth avoids schema/ORM drift. | Yes |
| `tenant_keys` table (per-tenant wrapped data keys, nil-UUID scope for global PII) | — (implied by ADR-0005) | Envelope encryption needs somewhere to keep wrapped keys; enables rotation and crypto-shredding. | Yes |
| `users` table RLS is ENABLED but not FORCED | FORCE everywhere | SECURITY DEFINER login functions (owner) must find a user before any tenant context exists. The app role is still fully bound. Every other tenant table is FORCEd. | Yes |
| Owner-only SELECT policy on `unsubscribe_tokens` | — | Public one-click unsubscribe must resolve a signature-verified token to its tenant without a session. Only the owner role (migrations, definer functions) gets it. | Yes |
| Scheduler (`system` role) may list tenants only when it has no tenant context | System sees all tenants | Found by the isolation tests: inside a tenant, jobs must not see other tenants. | — |
| In-process per-IP login rate limiter + per-account DB lockout | Valkey limiter | Enough for a single API instance in Phase 2; Valkey-backed limiter in Phase 6 (multiple instances). | Yes |
| Local test runs used Redis 7.0 (BSD-licensed) from the OS; Compose uses Valkey 8 | Valkey everywhere | Workspace availability only; no Redis-specific features are used. | n/a |
| Business-hours window 09:00–17:00 recipient-local, Mon–Fri, excluding the recipient's bank holidays; unknown UK nation avoids the union of all four nations' holidays | — | Safest default; window becomes tenant-configurable in Phase 3. | Yes |
| Next.js kept on the 15.5 line (15.5.27) with `pnpm.overrides` for postcss 8.5.29 / sharp 0.35.5 | Next 16 | Patched releases without a major upgrade; Next 16 evaluated separately. | Yes |
