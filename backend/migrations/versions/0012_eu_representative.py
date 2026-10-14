"""Phase 9 (Stage 2 readiness): the client's EU/EEA representative (GDPR Article 27), recorded by an operator.
Required before emailing prospects in a country whose rules say so, unless the client is itself established in
the EU/EEA. Only operators can write tenants (existing row-level policy).

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE tenants ADD COLUMN eu_representative jsonb;
ALTER TABLE tenants ADD CONSTRAINT tenants_eu_representative_shape CHECK (
  eu_representative IS NULL OR (eu_representative ? 'name' AND eu_representative ? 'contact_email'
    AND eu_representative ? 'country_code')
);
""")


def downgrade() -> None:
    # Refuse rather than delete data: the column is only removed while no representative is recorded.
    op.execute("""
DO $$BEGIN
  PERFORM set_config('app.role', 'operator', true);  -- see every tenant (forced row-level security)
  IF EXISTS (SELECT 1 FROM tenants WHERE eu_representative IS NOT NULL) THEN
    RAISE EXCEPTION 'an EU representative is recorded; refusing to drop it (export and clear it deliberately first)';
  END IF;
END$$;
ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_eu_representative_shape;
ALTER TABLE tenants DROP COLUMN IF EXISTS eu_representative;
""")
