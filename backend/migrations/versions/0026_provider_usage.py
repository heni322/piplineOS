"""Monthly call counter per paid provider (first: Google Places), so a cap set in the settings (by default Google's
free monthly allowance) is enforced across all workers: a call is made only after it has been counted under the cap.
Holds counts only, no personal data. Only the system role writes; operators can read.

Revision ID: 0026
Revises: 0025
"""

from alembic import op

from app.db.ddl import APP_ROLE

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE provider_usage (
  provider text NOT NULL CHECK (provider ~ '^[a-z0-9_.]{{2,60}}$'),
  period text NOT NULL CHECK (period ~ '^[0-9]{{4}}-[0-9]{{2}}$'),
  calls integer NOT NULL DEFAULT 0 CHECK (calls >= 0),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (provider, period)
);
ALTER TABLE provider_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY usage_read ON provider_usage FOR SELECT USING (app_role() IN ('system', 'operator'));
CREATE POLICY usage_insert ON provider_usage FOR INSERT WITH CHECK (app_role() = 'system');
CREATE POLICY usage_update ON provider_usage FOR UPDATE USING (app_role() = 'system') WITH CHECK (app_role() = 'system');
GRANT SELECT, INSERT, UPDATE ON provider_usage TO {APP_ROLE};
""")


def downgrade() -> None:
    # Refuse rather than delete data: the counts show what was spent this month.
    op.execute("""
DO $$BEGIN
  PERFORM set_config('app.role', 'system', true);
  IF EXISTS (SELECT 1 FROM provider_usage WHERE calls > 0) THEN
    RAISE EXCEPTION 'provider calls are counted; refusing to drop the counter';
  END IF;
END$$;
DROP TABLE provider_usage;
""")
