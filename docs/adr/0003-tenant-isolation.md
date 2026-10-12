# ADR-0003 — Postgres Row-Level Security for tenant isolation

**Status:** Proposed

## Context
Strict tenant isolation at the data layer is required, with tests proving cross-tenant reads fail. The global suppression list is the only cross-tenant data. The operator needs cross-tenant views.

## Decision
- Every tenant-owned table has `tenant_id uuid NOT NULL` and RLS `ENABLE` + `FORCE`.
- Policies: `USING (tenant_id = current_setting('app.tenant_id')::uuid)` and the same `WITH CHECK`.
- Operator policy: `USING (current_setting('app.role', true) = 'operator')` — set only by the API after authenticating an operator with MFA; every cross-tenant query is written to `audit_log`.
- DB roles: `pipelineos_owner` (migrations only, owns tables), `pipelineos_app` (runtime; no `BYPASSRLS`, not owner), `pipelineos_readonly` (reporting).
- Session context is set with `SET LOCAL` inside each transaction by a SQLAlchemy `after_begin` hook; if `app.tenant_id` is unset, `current_setting(..., true)` returns NULL and the policy matches nothing (**fail closed**).
- Application code *also* scopes by tenant (repository layer requires a `TenantContext`), so RLS is the second line of defence, not the only one.
- Workers set tenant context per task from the task payload; tasks without a tenant context run only against global tables.

## Alternatives
- Schema-per-tenant: stronger separation but painful migrations and cross-tenant reporting. Rejected.
- Database-per-tenant: too costly for lean budget.
- App-level scoping only: one missed `WHERE` leaks data. Rejected as sole control.

## Consequences
+ Isolation enforced even if app code has a bug.
− Every test must set context; connection pooling must use `SET LOCAL` (transaction-scoped), never `SET`.

## Tests (required)
Cross-tenant SELECT/UPDATE/DELETE/INSERT fail; missing context returns zero rows; operator context works and is audited; pooled connection does not leak context between requests.

## Reversibility
Medium — policies are migrations and can be changed, but removing RLS later would be a security regression.
