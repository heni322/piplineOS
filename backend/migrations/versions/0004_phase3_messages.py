"""Phase 3: subject-line options on messages, edited subject on approvals, approval/review bookkeeping.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE messages ADD COLUMN subject_options_enc bytea;          -- the 3 candidate subject lines (encrypted JSON)
ALTER TABLE messages ADD COLUMN sources_snapshot jsonb;             -- source ids/urls the writer was given (no PII)
ALTER TABLE approvals ADD COLUMN original_subject_enc bytea;
ALTER TABLE approvals ADD COLUMN edited_subject_enc bytea;
ALTER TABLE approvals ADD COLUMN sampled boolean NOT NULL DEFAULT false;  -- auto-approved under sampling mode
ALTER TABLE approvals ALTER COLUMN reviewer_id DROP NOT NULL;
ALTER TABLE approvals ADD CONSTRAINT human_unless_sampled CHECK (sampled OR reviewer_id IS NOT NULL);
CREATE INDEX messages_pending ON messages (tenant_id, created_at) WHERE status = 'pending_approval';
""")


def downgrade() -> None:
    op.execute("""
DROP INDEX IF EXISTS messages_pending;
ALTER TABLE approvals DROP CONSTRAINT IF EXISTS human_unless_sampled;
-- Fails (safely, no data deleted) if sampled approvals exist; resolve them manually first.
ALTER TABLE approvals ALTER COLUMN reviewer_id SET NOT NULL;
ALTER TABLE approvals DROP COLUMN sampled, DROP COLUMN edited_subject_enc, DROP COLUMN original_subject_enc;
ALTER TABLE messages DROP COLUMN sources_snapshot, DROP COLUMN subject_options_enc;
""")
