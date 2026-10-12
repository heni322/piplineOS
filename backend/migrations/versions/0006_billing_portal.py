"""Phase 5: client sign-offs of terms, supplier profile, invoice numbering, draft/issued invoice integrity,
voidable lines and credits, dispute details.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

from app.db.ddl import APP_ROLE, PK, TID, grant_crud, tenant_rls

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

WRITERS = "app_role() IN ('operator','system')"


def upgrade() -> None:
    op.execute(f"""
-- What the client agreed to (qualification criteria, pricing), as an immutable snapshot + hash.
CREATE TABLE tenant_signoffs (
  {PK}, {TID},
  kind text NOT NULL CHECK (kind IN ('qualification_criteria','pricing')),
  config_id uuid NOT NULL REFERENCES tenant_configs(id),
  content jsonb NOT NULL,
  content_hash text NOT NULL,
  signed_by uuid NOT NULL REFERENCES users(id),
  method text NOT NULL CHECK (method IN ('portal','recorded_by_operator')),
  evidence_ref text,
  signed_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT recorded_signoff_has_evidence CHECK (method = 'portal' OR length(trim(coalesce(evidence_ref,''))) >= 3)
);
CREATE INDEX tenant_signoffs_lookup ON tenant_signoffs (tenant_id, kind, signed_at DESC);
CREATE INDEX tenant_signoffs_config ON tenant_signoffs (config_id);
CREATE INDEX tenant_signoffs_signed_by ON tenant_signoffs (signed_by);
GRANT SELECT, INSERT ON tenant_signoffs TO {APP_ROLE};
CREATE TRIGGER tenant_signoffs_append_only BEFORE UPDATE OR DELETE ON tenant_signoffs
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

-- The issuer's own details (from config/supplier.yaml), versioned and append-only.
CREATE TABLE supplier_profiles (
  {PK},
  version integer NOT NULL UNIQUE CHECK (version > 0),
  content_hash text NOT NULL,
  legal_name text NOT NULL,
  company_number text,
  registered_address text NOT NULL,
  country_code text NOT NULL,
  vat_number text,
  billing_email text NOT NULL,
  invoice_prefix text NOT NULL CHECK (invoice_prefix ~ '^[A-Z0-9-]{{2,12}}$'),
  payment_details text,
  placeholder boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT ON supplier_profiles TO {APP_ROLE};
CREATE TRIGGER supplier_profiles_append_only BEFORE UPDATE OR DELETE ON supplier_profiles
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

-- Gapless invoice numbers per prefix and year, assigned only when an invoice is issued (row-locked).
CREATE TABLE invoice_counters (
  prefix text NOT NULL,
  year integer NOT NULL,
  last_number integer NOT NULL DEFAULT 0 CHECK (last_number >= 0),
  PRIMARY KEY (prefix, year)
);
GRANT SELECT, INSERT, UPDATE ON invoice_counters TO {APP_ROLE};

ALTER TABLE invoices ADD COLUMN period_start date;
ALTER TABLE invoices ADD COLUMN period_end date;
ALTER TABLE invoices ADD COLUMN customer_country text;
ALTER TABLE invoices ADD COLUMN blocked_reasons text[] NOT NULL DEFAULT '{{}}';
ALTER TABLE invoices ADD COLUMN issued_at timestamptz;
ALTER TABLE invoices ADD COLUMN issued_by uuid REFERENCES users(id);
ALTER TABLE invoices ADD COLUMN void_reason text;
CREATE INDEX invoices_issued_by ON invoices (issued_by);
CREATE UNIQUE INDEX invoices_one_live_per_period ON invoices (tenant_id, period_start) WHERE status <> 'void';
ALTER TABLE invoices ADD CONSTRAINT issued_has_number CHECK (status = 'draft' OR status = 'void'
  OR (issued_at IS NOT NULL AND number !~ '^DRAFT-'));
ALTER TABLE invoices ADD CONSTRAINT void_has_reason CHECK (status <> 'void' OR length(trim(coalesce(void_reason,''))) >= 3);

ALTER TABLE invoice_lines DROP CONSTRAINT invoice_lines_meeting_id_key;
ALTER TABLE invoice_lines ADD COLUMN voided_at timestamptz;
ALTER TABLE invoice_lines ADD COLUMN credit_for_line_id uuid REFERENCES invoice_lines(id);
ALTER TABLE invoice_lines ADD CONSTRAINT credit_line_shape CHECK (kind <> 'credit'
  OR (credit_for_line_id IS NOT NULL AND amount_minor < 0));
ALTER TABLE invoice_lines ADD CONSTRAINT charge_line_positive CHECK (kind = 'credit' OR amount_minor >= 0);
CREATE UNIQUE INDEX invoice_lines_meeting_once ON invoice_lines (meeting_id) WHERE kind = 'meeting' AND voided_at IS NULL;
CREATE UNIQUE INDEX invoice_lines_credit_once ON invoice_lines (credit_for_line_id) WHERE voided_at IS NULL;
CREATE UNIQUE INDEX invoice_lines_setup_once ON invoice_lines (tenant_id) WHERE kind = 'setup' AND voided_at IS NULL;
CREATE INDEX invoice_lines_meeting ON invoice_lines (meeting_id);

ALTER TABLE disputes ADD COLUMN kind text NOT NULL DEFAULT 'other' CHECK (kind IN ('attendance','qualification','other'));
ALTER TABLE disputes ADD COLUMN window_ends_at timestamptz;
ALTER TABLE disputes ADD COLUMN credit_line_id uuid REFERENCES invoice_lines(id);
CREATE UNIQUE INDEX disputes_one_open_per_meeting ON disputes (meeting_id) WHERE status = 'open';
CREATE INDEX disputes_credit_line ON disputes (credit_line_id);

-- Issued invoices are immutable (UK invoice rules): only issued -> paid / void, nothing else changes.
CREATE FUNCTION protect_issued_invoice() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'invoices are never deleted (void instead)' USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'draft' THEN RETURN NEW; END IF;
  IF OLD.status IN ('paid','void') OR NOT (NEW.status IN ('paid','void', OLD.status)) OR
     (to_jsonb(OLD) - 'status' - 'void_reason' - 'updated_at') IS DISTINCT FROM
     (to_jsonb(NEW) - 'status' - 'void_reason' - 'updated_at') THEN
    RAISE EXCEPTION 'issued invoice % is immutable', OLD.number USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER invoices_protect_issued BEFORE UPDATE OR DELETE ON invoices
  FOR EACH ROW EXECUTE FUNCTION protect_issued_invoice();

CREATE FUNCTION protect_invoice_lines() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'invoice lines are never deleted (void instead)' USING ERRCODE = '42501';
  END IF;
  SELECT status INTO st FROM invoices WHERE id = COALESCE(NEW.invoice_id, OLD.invoice_id);
  IF st <> 'draft' AND NOT (TG_OP = 'UPDATE' AND st = 'void'
      AND (to_jsonb(OLD) - 'voided_at') = (to_jsonb(NEW) - 'voided_at')) THEN
    RAISE EXCEPTION 'lines of a % invoice cannot change', st USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER invoice_lines_protect BEFORE INSERT OR UPDATE OR DELETE ON invoice_lines
  FOR EACH ROW EXECUTE FUNCTION protect_invoice_lines();

-- Only the operator or the system writes invoices; clients read their own (RLS) and raise disputes.
CREATE POLICY writers_insert ON invoices AS RESTRICTIVE FOR INSERT WITH CHECK ({WRITERS});
CREATE POLICY writers_update ON invoices AS RESTRICTIVE FOR UPDATE USING ({WRITERS}) WITH CHECK ({WRITERS});
CREATE POLICY writers_delete ON invoices AS RESTRICTIVE FOR DELETE USING ({WRITERS});
CREATE POLICY writers_insert ON invoice_lines AS RESTRICTIVE FOR INSERT WITH CHECK ({WRITERS});
CREATE POLICY writers_update ON invoice_lines AS RESTRICTIVE FOR UPDATE USING ({WRITERS}) WITH CHECK ({WRITERS});
CREATE POLICY writers_delete ON invoice_lines AS RESTRICTIVE FOR DELETE USING ({WRITERS});
CREATE POLICY raise_or_resolve ON disputes AS RESTRICTIVE FOR INSERT
  WITH CHECK ({WRITERS} OR app_role() = 'tenant_admin');
CREATE POLICY resolve_only_operator ON disputes AS RESTRICTIVE FOR UPDATE USING ({WRITERS}) WITH CHECK ({WRITERS});
CREATE POLICY no_delete ON disputes AS RESTRICTIVE FOR DELETE USING (false);
CREATE POLICY signer ON tenant_signoffs AS RESTRICTIVE FOR INSERT
  WITH CHECK ((app_role() = 'tenant_admin' AND method = 'portal' AND signed_by = app_user_id())
           OR (app_is_operator() AND method = 'recorded_by_operator' AND signed_by = app_user_id()));
""")
    op.execute(tenant_rls("tenant_signoffs"))
    _ = grant_crud  # tables above use narrower grants on purpose


def downgrade() -> None:
    op.execute("""
DO $$BEGIN
  IF EXISTS (SELECT 1 FROM invoices WHERE status <> 'draft') OR EXISTS (SELECT 1 FROM tenant_signoffs) THEN
    RAISE EXCEPTION 'refusing to downgrade: issued invoices or client sign-offs exist (no data is deleted)';
  END IF;
END$$;
DROP POLICY IF EXISTS signer ON tenant_signoffs;
DROP POLICY IF EXISTS no_delete ON disputes;
DROP POLICY IF EXISTS resolve_only_operator ON disputes;
DROP POLICY IF EXISTS raise_or_resolve ON disputes;
DROP POLICY IF EXISTS writers_delete ON invoice_lines;
DROP POLICY IF EXISTS writers_update ON invoice_lines;
DROP POLICY IF EXISTS writers_insert ON invoice_lines;
DROP POLICY IF EXISTS writers_delete ON invoices;
DROP POLICY IF EXISTS writers_update ON invoices;
DROP POLICY IF EXISTS writers_insert ON invoices;
DROP TRIGGER IF EXISTS invoice_lines_protect ON invoice_lines;
DROP FUNCTION IF EXISTS protect_invoice_lines();
DROP TRIGGER IF EXISTS invoices_protect_issued ON invoices;
DROP FUNCTION IF EXISTS protect_issued_invoice();
DROP INDEX IF EXISTS disputes_credit_line;
DROP INDEX IF EXISTS disputes_one_open_per_meeting;
ALTER TABLE disputes DROP COLUMN credit_line_id, DROP COLUMN window_ends_at, DROP COLUMN kind;
DROP INDEX IF EXISTS invoice_lines_meeting;
DROP INDEX IF EXISTS invoice_lines_setup_once;
DROP INDEX IF EXISTS invoice_lines_credit_once;
DROP INDEX IF EXISTS invoice_lines_meeting_once;
ALTER TABLE invoice_lines DROP CONSTRAINT IF EXISTS charge_line_positive;
ALTER TABLE invoice_lines DROP CONSTRAINT IF EXISTS credit_line_shape;
-- Fails (safely, nothing deleted) if voided draft lines left a meeting on two rows; resolve manually first.
ALTER TABLE invoice_lines DROP COLUMN credit_for_line_id, DROP COLUMN voided_at;
ALTER TABLE invoice_lines ADD CONSTRAINT invoice_lines_meeting_id_key UNIQUE (meeting_id);
ALTER TABLE invoices DROP CONSTRAINT IF EXISTS void_has_reason;
ALTER TABLE invoices DROP CONSTRAINT IF EXISTS issued_has_number;
DROP INDEX IF EXISTS invoices_one_live_per_period;
DROP INDEX IF EXISTS invoices_issued_by;
ALTER TABLE invoices DROP COLUMN void_reason, DROP COLUMN issued_by, DROP COLUMN issued_at,
  DROP COLUMN blocked_reasons, DROP COLUMN customer_country, DROP COLUMN period_end, DROP COLUMN period_start;
DROP TABLE IF EXISTS invoice_counters;
DROP TABLE IF EXISTS supplier_profiles;
DROP TABLE IF EXISTS tenant_signoffs;
""")
