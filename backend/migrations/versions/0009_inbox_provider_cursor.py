"""Phase 6.1: Google Workspace inboxes keep the last Gmail historyId processed, so a push notification only fetches
what is new. Inbox providers are limited to the ones PipelineOS supports.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE inboxes ADD COLUMN history_id bigint;
ALTER TABLE inboxes ADD CONSTRAINT inboxes_provider_known CHECK (provider IN ('mock', 'msgraph', 'google'));
""")


def downgrade() -> None:
    op.execute("""
ALTER TABLE inboxes DROP CONSTRAINT IF EXISTS inboxes_provider_known;
ALTER TABLE inboxes DROP COLUMN IF EXISTS history_id;
""")
