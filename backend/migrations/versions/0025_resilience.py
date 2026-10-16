"""Resilience (ADR-0019): bounded, backed-off retries for sends and outbox events, and escalation reasons for work
that gave up after its retries.

- messages.send_attempts / next_attempt_at: a provider refusal is retried with exponential backoff, at most
  MAX_SEND_ATTEMPTS times; then the message is failed and a human is told (never retried forever).
- outbox.next_attempt_at: a failing event backs off; after MAX_ATTEMPTS it is dead-lettered (left for a human,
  counted by a metric) instead of blocking the events behind it.

Revision ID: 0025
Revises: 0024
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_BASE = (
    "'anger','legal','pricing','contract','complaint','low_confidence','off_playbook','llm_failure',"
    "'reschedule_limit','needs_reconciliation','other','data_protection','unsupported_language',"
    "'calendar_disconnected','prompt_injection'"
)


def upgrade() -> None:
    op.execute(f"""
ALTER TABLE messages ADD COLUMN send_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE messages ADD COLUMN next_attempt_at timestamptz;
ALTER TABLE outbox ADD COLUMN next_attempt_at timestamptz;
ALTER TABLE escalations DROP CONSTRAINT escalations_reason_check;
ALTER TABLE escalations ADD CONSTRAINT escalations_reason_check CHECK (reason IN ({_BASE},
  'send_failed','inbound_failed'));
""")


def downgrade() -> None:
    op.execute(f"""
UPDATE escalations SET reason = 'other' WHERE reason IN ('send_failed','inbound_failed');
ALTER TABLE escalations DROP CONSTRAINT escalations_reason_check;
ALTER TABLE escalations ADD CONSTRAINT escalations_reason_check CHECK (reason IN ({_BASE}));
ALTER TABLE outbox DROP COLUMN next_attempt_at;
ALTER TABLE messages DROP COLUMN next_attempt_at;
ALTER TABLE messages DROP COLUMN send_attempts;
""")
