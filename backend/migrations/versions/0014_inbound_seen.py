"""Replies safety net: a keyed hash of the Message-ID of every inbound message from an UNKNOWN sender that was
handled. Matched replies are already de-duplicated by `replies.provider_message_id`; unknown senders are not stored,
so without this a duplicate push or the 15-minute sweep would handle the same message twice (for example, open the
same data-protection request twice). Only an HMAC is kept: nothing readable.

Revision ID: 0014
Revises: 0013
"""

from alembic import op

from app.db.ddl import APP_ROLE, tenant_rls

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE inbound_seen (
  tenant_id uuid NOT NULL REFERENCES tenants (id),
  message_hash bytea NOT NULL,
  seen_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, message_hash)
);
{tenant_rls("inbound_seen")}
GRANT SELECT, INSERT ON inbound_seen TO {APP_ROLE};
""")


def downgrade() -> None:
    op.execute("""
DO $$BEGIN
  PERFORM set_config('app.role', 'operator', true);
  IF EXISTS (SELECT 1 FROM inbound_seen) THEN
    RAISE EXCEPTION 'inbound_seen holds data; refusing to drop it (clear it deliberately first)';
  END IF;
END$$;
DROP TABLE inbound_seen;
""")
