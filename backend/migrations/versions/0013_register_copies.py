"""Phase 10 (Stage 4 readiness): local copies of public company registers that are published as free open-data
files rather than lookup APIs: Belgium (KBO/BCE open data) and Ireland (CRO open data, CC BY 4.0).

LEGAL PERSONS ONLY, and only number, official names, legal form and status. A natural person's entry (a sole
trader) is never imported, so it stays "unknown" and blocked; addresses and other fields are not imported.
Reference data, not tenant data: readable by the application, written only by the owner's import command.

Revision ID: 0013
Revises: 0012
"""

from alembic import op

from app.db.ddl import APP_ROLE

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE register_companies (
  country_code text NOT NULL CHECK (country_code ~ '^[A-Z]{{2}}$'),
  company_number text NOT NULL,
  form_code text,
  legal_form text NOT NULL,
  status text NOT NULL,
  extract_date date,
  imported_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (country_code, company_number)
);
CREATE TABLE register_names (
  country_code text NOT NULL,
  company_number text NOT NULL,
  name text NOT NULL,
  name_norm text NOT NULL,
  PRIMARY KEY (country_code, company_number, name_norm),
  FOREIGN KEY (country_code, company_number) REFERENCES register_companies (country_code, company_number)
);
CREATE INDEX register_names_norm ON register_names (country_code, name_norm);
GRANT SELECT ON register_companies, register_names TO {APP_ROLE};
""")


def downgrade() -> None:
    # Refuse rather than delete data: only removed while empty (re-import from the open-data files otherwise).
    op.execute("""
DO $$BEGIN
  IF EXISTS (SELECT 1 FROM register_companies) THEN
    RAISE EXCEPTION 'register_companies holds data; refusing to drop it (clear it deliberately first)';
  END IF;
END$$;
DROP TABLE register_names;
DROP TABLE register_companies;
""")
