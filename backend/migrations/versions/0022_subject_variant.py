"""Subject-line A/B test: which of the writer's subject lines a first email used (0-based), so replies and positive
replies per 100 people reached can be compared per variant. NULL for follow-ups and messages from before the test.

Revision ID: 0022
Revises: 0021
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN subject_variant smallint CHECK (subject_variant BETWEEN 0 AND 2);")


def downgrade() -> None:
    op.execute("ALTER TABLE messages DROP COLUMN subject_variant;")
