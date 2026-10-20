"""Inbox placement checks (go-live runbook C2), recorded instead of only described: before a campaign and for each
new mailbox, the operator sends a copy of an approved email to a placement checker and to a few of their own
mailboxes, and records the result here. Append-only (a result is never edited; a new check is recorded instead),
tenant-isolated, and only operators can write.

Revision ID: 0025
Revises: 0024
"""

from alembic import op

from app.db.ddl import PK, TID, grant_append_only, tenant_rls

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

WRITERS = "app_role() = 'operator'"


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE inbox_placement_checks (
  {PK}, {TID},
  inbox_id uuid NOT NULL REFERENCES inboxes(id),
  score numeric(3,1) CHECK (score IS NULL OR score BETWEEN 0 AND 10),
  spf_pass boolean NOT NULL,
  dkim_pass boolean NOT NULL,
  dmarc_pass boolean NOT NULL,
  blacklisted boolean NOT NULL,
  provider_results jsonb NOT NULL,
  evidence_url text,
  passed boolean NOT NULL,
  reasons jsonb NOT NULL DEFAULT '[]',
  recorded_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX inbox_placement_checks_inbox ON inbox_placement_checks (inbox_id, created_at DESC);
CREATE INDEX inbox_placement_checks_tenant ON inbox_placement_checks (tenant_id);
{tenant_rls("inbox_placement_checks")}
CREATE POLICY operators_insert ON inbox_placement_checks AS RESTRICTIVE FOR INSERT WITH CHECK ({WRITERS});
{grant_append_only("inbox_placement_checks")}
""")


def downgrade() -> None:
    # Refuse rather than delete data: only removed while no check has been recorded.
    op.execute("""
DO $$BEGIN
  PERFORM set_config('app.role', 'operator', true);
  IF EXISTS (SELECT 1 FROM inbox_placement_checks) THEN
    RAISE EXCEPTION 'placement checks are recorded; refusing to drop them';
  END IF;
END$$;
DROP TABLE inbox_placement_checks;
""")
