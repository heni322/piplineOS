"""Company searches from the operator console ("Find companies"): a Google Places search by activity and area, run
in the background. An operator asks (insert); only the worker (system role) records the result (update). Each row
keeps the query, the counts, and the companies confirmed by their register and their own website: the register's
name and number, the verified domain and the generic business address the site publishes. Nothing Google returned
is kept (its terms). Tenant-isolated; never deleted by the app.

Revision ID: 0027
Revises: 0026
"""

from alembic import op

from app.db.ddl import APP_ROLE, PK, TID, tenant_rls

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE company_searches (
  {PK}, {TID},
  query text NOT NULL CHECK (length(query) BETWEEN 3 AND 200),
  country text NOT NULL CHECK (country IN ('GB', 'FR')),
  pages smallint NOT NULL DEFAULT 1 CHECK (pages BETWEEN 1 AND 3),
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'dry_run', 'failed')),
  called_google boolean,
  report jsonb,
  error text,
  requested_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX company_searches_tenant ON company_searches (tenant_id, created_at DESC);
{tenant_rls("company_searches")}
CREATE POLICY operators_insert ON company_searches AS RESTRICTIVE FOR INSERT WITH CHECK (app_role() = 'operator');
CREATE POLICY system_update ON company_searches AS RESTRICTIVE FOR UPDATE
  USING (app_role() = 'system') WITH CHECK (app_role() = 'system');
GRANT SELECT, INSERT, UPDATE ON company_searches TO {APP_ROLE};
""")


def downgrade() -> None:
    # Refuse rather than delete data: only removed while no search has been made.
    op.execute("""
DO $$BEGIN
  PERFORM set_config('app.role', 'operator', true);  -- operators see every tenant's rows
  IF EXISTS (SELECT 1 FROM company_searches) THEN
    RAISE EXCEPTION 'company searches are recorded; refusing to drop them';
  END IF;
END$$;
DROP TABLE company_searches;
""")
