"""Each client admin can turn the weekly results email off (and back on) in the portal. On by default.

Revision ID: 0024
Revises: 0023
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN weekly_report boolean NOT NULL DEFAULT true;")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN weekly_report;")
