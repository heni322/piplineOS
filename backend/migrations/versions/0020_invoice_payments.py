"""Payments received against issued invoices (bank transfer first; a payment provider later).

Append-only: a mistake is corrected with a reversing entry (negative amount), never edited. An invoice becomes
'paid' when what was received covers its total; it never goes back (UK invoice rules: issued invoices are
immutable apart from issued -> paid / void).

Revision ID: 0020
Revises: 0019
"""

from alembic import op

from app.db.ddl import grant_append_only, tenant_rls

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE invoice_payments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  invoice_id uuid NOT NULL REFERENCES invoices(id),
  amount_minor bigint NOT NULL CHECK (amount_minor <> 0),
  currency char(3) NOT NULL CHECK (currency IN ('GBP','EUR')),
  received_on date NOT NULL,
  method text NOT NULL CHECK (method IN ('bank_transfer','card','direct_debit','other','reversal')),
  reference text CHECK (char_length(reference) <= 100),
  recorded_by uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX invoice_payments_invoice ON invoice_payments (invoice_id);
{tenant_rls("invoice_payments")}
{grant_append_only("invoice_payments")}
""")


def downgrade() -> None:
    op.execute("""
DO $$BEGIN
  IF EXISTS (SELECT 1 FROM invoice_payments) THEN
    RAISE EXCEPTION 'invoice_payments holds records of money received; refusing to drop it';
  END IF;
END$$;
DROP TABLE invoice_payments;
""")
