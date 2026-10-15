"""Phase 12: market-wide trigger signals from the Companies House filing stream (free, same API key).

`register_filing_events` keeps only filings that are buying triggers for a company: a new share allotment (SH01,
usually a funding round) and a new charge (MR01, new borrowing). For each: the company's number, name, status and
SIC codes, the filing type and date, and the capital figure stated on the form. COMPANIES ONLY: no officer,
shareholder or other personal data is read or stored. Reference data shared by every client, like
register_companies: each client's hunt reads the rows matching its own SIC codes.

`stream_cursors` remembers where the ingest job stopped (the stream's timepoint), so nothing is missed or read twice.

Revision ID: 0016
Revises: 0015
"""

from alembic import op

from app.db.ddl import APP_ROLE

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE register_filing_events (
  id bigserial PRIMARY KEY,
  company_number text NOT NULL CHECK (company_number ~ '^[A-Z0-9]{{8}}$'),
  company_name text NOT NULL,
  company_status text NOT NULL,
  sic_codes text[] NOT NULL DEFAULT '{{}}',
  filing_type text NOT NULL CHECK (filing_type IN ('SH01', 'MR01')),
  filed_on date NOT NULL,
  capital text,
  transaction_ref text NOT NULL,
  timepoint bigint NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (company_number, transaction_ref)
);
CREATE INDEX register_filing_events_sic ON register_filing_events USING gin (sic_codes);
CREATE INDEX register_filing_events_filed ON register_filing_events (filed_on);
CREATE TABLE stream_cursors (
  stream text PRIMARY KEY,
  timepoint bigint NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT ON register_filing_events TO {APP_ROLE};
GRANT USAGE ON SEQUENCE register_filing_events_id_seq TO {APP_ROLE};
GRANT SELECT, INSERT, UPDATE ON stream_cursors TO {APP_ROLE};
""")


def downgrade() -> None:
    # Refuse rather than delete data: the stream only replays a limited window, so these rows can't be rebuilt.
    op.execute("""
DO $$BEGIN
  IF EXISTS (SELECT 1 FROM register_filing_events) THEN
    RAISE EXCEPTION 'register_filing_events holds data; refusing to drop it (clear it deliberately first)';
  END IF;
END$$;
DROP TABLE stream_cursors;
DROP TABLE register_filing_events;
""")
