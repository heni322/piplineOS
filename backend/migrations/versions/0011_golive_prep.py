"""Phase 7 (go-live preparation): DNS check results for sending domains, and the last day an inbox's warm-up
advanced (so it advances at most once a day).

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE sending_domains ADD COLUMN dns_issues jsonb NOT NULL DEFAULT '[]';
ALTER TABLE sending_domains ADD COLUMN dns_checked_at timestamptz;
ALTER TABLE inboxes ADD COLUMN warmup_last_day date;
""")


def downgrade() -> None:
    # These columns only hold results that can be recomputed (a DNS check, a warm-up step date).
    op.execute("""
ALTER TABLE inboxes DROP COLUMN IF EXISTS warmup_last_day;
ALTER TABLE sending_domains DROP COLUMN IF EXISTS dns_checked_at;
ALTER TABLE sending_domains DROP COLUMN IF EXISTS dns_issues;
""")
