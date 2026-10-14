"""Source health: one row per run of a signal source (per client hunt) or of the market-wide filing ingest.

System data for the operator: source name, when, how many items came back, the outcome and, on failure, the error
type and HTTP status only. Never response bodies, company lists or anything about people.

Revision ID: 0018
Revises: 0017
"""

from alembic import op

from app.db.ddl import APP_ROLE

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE source_runs (
  id bigserial PRIMARY KEY,
  source text NOT NULL,
  started_at timestamptz NOT NULL,
  finished_at timestamptz NOT NULL DEFAULT now(),
  outcome text NOT NULL CHECK (outcome IN ('ok', 'partial', 'rate_limited', 'failed')),
  fetched integer NOT NULL DEFAULT 0 CHECK (fetched >= 0),
  error text CHECK (char_length(error) <= 200),
  detail jsonb NOT NULL DEFAULT '{{}}'
);
CREATE INDEX source_runs_recent ON source_runs (source, finished_at DESC);
GRANT SELECT, INSERT ON source_runs TO {APP_ROLE};
GRANT USAGE ON SEQUENCE source_runs_id_seq TO {APP_ROLE};
""")


def downgrade() -> None:
    op.execute("DROP TABLE source_runs;")
