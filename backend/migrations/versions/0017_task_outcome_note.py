"""Contact tasks: the operator's note on how a task was closed, kept on the task itself (tenant data, under RLS)
rather than in the append-only audit log, where free text could fix personal data in place for good.

Revision ID: 0017
Revises: 0016
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE alternative_channel_tasks ADD COLUMN outcome_note text CHECK (char_length(outcome_note) <= 1000);
ALTER TABLE alternative_channel_tasks ADD COLUMN closed_at timestamptz;
""")


def downgrade() -> None:
    op.execute("""
ALTER TABLE alternative_channel_tasks DROP COLUMN closed_at;
ALTER TABLE alternative_channel_tasks DROP COLUMN outcome_note;
""")
