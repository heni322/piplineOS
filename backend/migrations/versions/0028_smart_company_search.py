"""Smart company search: the AI plans the Google Places searches from the client's targeting and ranks the companies
the checks confirmed. A smart search is flagged; the searches the AI wrote, and each company's fit score and reason,
are kept in the existing `report` (no data from Google). Existing searches stay manual.

Revision ID: 0028
Revises: 0027
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE company_searches ADD COLUMN smart boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    # Refuse rather than lose which searches were smart.
    op.execute("""
DO $$BEGIN
  PERFORM set_config('app.role', 'operator', true);  -- operators see every tenant's rows
  IF EXISTS (SELECT 1 FROM company_searches WHERE smart) THEN
    RAISE EXCEPTION 'smart company searches are recorded; refusing to drop the flag';
  END IF;
END$$;
ALTER TABLE company_searches DROP COLUMN smart;
""")
