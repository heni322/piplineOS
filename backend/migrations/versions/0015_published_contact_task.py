"""Free-only contacts: a decision-maker found on a public register has a name but no email address. Instead of
being dropped, the lead becomes a human task to reach them through the company's own published channels (its
website contact form or switchboard, or a letter to the registered office), or to get a business address from the
client. No automated messaging, as for every alternative-channel task.

Revision ID: 0015
Revises: 0014
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE alternative_channel_tasks DROP CONSTRAINT alternative_channel_tasks_channel_check;
ALTER TABLE alternative_channel_tasks ADD CONSTRAINT alternative_channel_tasks_channel_check
  CHECK (channel IN ('linkedin_manual','warm_intro','event','inbound_content','published_contact'));
""")


def downgrade() -> None:
    op.execute("""
DO $$BEGIN
  PERFORM set_config('app.role', 'operator', true);
  IF EXISTS (SELECT 1 FROM alternative_channel_tasks WHERE channel = 'published_contact') THEN
    RAISE EXCEPTION 'published_contact tasks exist; refusing to downgrade (resolve them deliberately first)';
  END IF;
END$$;
ALTER TABLE alternative_channel_tasks DROP CONSTRAINT alternative_channel_tasks_channel_check;
ALTER TABLE alternative_channel_tasks ADD CONSTRAINT alternative_channel_tasks_channel_check
  CHECK (channel IN ('linkedin_manual','warm_intro','event','inbound_content'));
""")
