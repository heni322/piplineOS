"""Inbound pilot requests from the public landing page: a company asking us to contact it (consent-based, the
opposite of cold outreach). Name, email and message are encrypted; anyone may submit (insert only), only the
operator may read or update. Retention: LEGAL REVIEW REQUIRED (docs/LEGAL-REVIEW-CHECKLIST.md).

Revision ID: 0023
Revises: 0022
"""

from alembic import op

from app.db.ddl import APP_ROLE

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE pilot_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company text NOT NULL CHECK (char_length(company) BETWEEN 2 AND 200),
  name_enc bytea NOT NULL,
  email_enc bytea NOT NULL,
  message_enc bytea,
  consent_text text NOT NULL,
  status text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'contacted', 'closed')),
  handled_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pilot_requests_created ON pilot_requests (created_at DESC);
ALTER TABLE pilot_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE pilot_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY anyone_submits ON pilot_requests FOR INSERT WITH CHECK (status = 'new' AND handled_by IS NULL);
CREATE POLICY operator_reads ON pilot_requests FOR SELECT USING (app_is_operator());
CREATE POLICY operator_updates ON pilot_requests FOR UPDATE USING (app_is_operator()) WITH CHECK (app_is_operator());
GRANT SELECT, INSERT, UPDATE ON pilot_requests TO {APP_ROLE};
""")


def downgrade() -> None:
    op.execute("DROP TABLE pilot_requests;")
