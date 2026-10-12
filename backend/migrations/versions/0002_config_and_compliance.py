"""Versioned legal/tax/governance configuration, suppression list, go-live checklist.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

from app.db.ddl import APP_ROLE, PK, grant_append_only

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

VERSIONED = "version integer NOT NULL CHECK (version > 0), content_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()"


def upgrade() -> None:
    op.execute(f"""
-- ---------------------------------------------------------------- compliance_rules (versioned, append-only)
CREATE TABLE compliance_rules (
  {PK},
  country_code text NOT NULL,
  {VERSIONED},
  mode text NOT NULL CHECK (mode IN ('permitted_with_opt_out','consent_required','restricted_review')),
  allowed_channels text[] NOT NULL DEFAULT '{{}}',
  subscriber_rules jsonb NOT NULL DEFAULT '{{}}',
  required_message_elements text[] NOT NULL DEFAULT '{{}}',
  regime text NOT NULL CHECK (regime IN ('uk_gdpr','eu_gdpr','other')),
  notes text,
  source text,
  verified_by_counsel boolean NOT NULL DEFAULT false,
  verified_by uuid REFERENCES users(id),
  verified_at timestamptz,
  last_reviewed date,
  effective_from timestamptz NOT NULL DEFAULT now(),
  UNIQUE (country_code, version),
  CONSTRAINT counsel_attestation CHECK (NOT verified_by_counsel OR (verified_by IS NOT NULL AND verified_at IS NOT NULL))
);
CREATE INDEX compliance_rules_verified_by ON compliance_rules (verified_by);
CREATE VIEW current_compliance_rules AS
  SELECT DISTINCT ON (country_code) * FROM compliance_rules ORDER BY country_code, version DESC;
{grant_append_only("compliance_rules")}
GRANT SELECT ON current_compliance_rules TO {APP_ROLE};

-- ---------------------------------------------------------------- country_rollout_status (versioned)
CREATE TABLE country_rollout_status (
  {PK},
  country_code text NOT NULL,
  {VERSIONED},
  name text NOT NULL,
  stage text NOT NULL CHECK (stage IN ('1','2','3','4','restricted','unplanned')),
  enabled boolean NOT NULL DEFAULT false,
  enabled_by uuid REFERENCES users(id),
  enabled_at timestamptz,
  languages text[] NOT NULL DEFAULT '{{}}',
  currency char(3),
  time_zones text[] NOT NULL DEFAULT '{{}}',
  holiday_calendars text[] NOT NULL DEFAULT '{{}}',
  eu_representative_required boolean NOT NULL DEFAULT false,
  notes text,
  UNIQUE (country_code, version),
  CONSTRAINT enabling_is_attributed CHECK (NOT enabled OR (enabled_by IS NOT NULL AND enabled_at IS NOT NULL))
);
CREATE INDEX country_rollout_enabled_by ON country_rollout_status (enabled_by);
CREATE VIEW current_country_rollout AS
  SELECT DISTINCT ON (country_code) * FROM country_rollout_status ORDER BY country_code, version DESC;
{grant_append_only("country_rollout_status")}
GRANT SELECT ON current_country_rollout TO {APP_ROLE};

-- ---------------------------------------------------------------- tax (versioned, verified_by_accountant=false)
CREATE TABLE tax_rules (
  {PK},
  code text NOT NULL,
  {VERSIONED},
  applies_when jsonb NOT NULL DEFAULT '{{}}',
  rate_bps integer NOT NULL CHECK (rate_bps BETWEEN 0 AND 10000),
  invoice_wording text,
  verified_by_accountant boolean NOT NULL DEFAULT false,
  verified_by uuid REFERENCES users(id),
  last_reviewed date,
  source text,
  UNIQUE (code, version),
  CONSTRAINT accountant_attestation CHECK (NOT verified_by_accountant OR verified_by IS NOT NULL)
);
CREATE INDEX tax_rules_verified_by ON tax_rules (verified_by);
{grant_append_only("tax_rules")}

CREATE TABLE tax_settings (
  {PK},
  {VERSIONED},
  uk_vat_registered boolean NOT NULL DEFAULT false,
  uk_vat_number text,
  uk_vat_registration_threshold_minor bigint,
  threshold_currency char(3) NOT NULL DEFAULT 'GBP',
  warn_at_pct integer[] NOT NULL DEFAULT '{{80,90}}',
  verified_by_accountant boolean NOT NULL DEFAULT false,
  source text,
  UNIQUE (version)
);
{grant_append_only("tax_settings")}

-- ---------------------------------------------------------------- transfers & sub-processors (versioned)
CREATE TABLE transfer_mechanisms (
  {PK},
  key text NOT NULL,
  {VERSIONED},
  kind text NOT NULL CHECK (kind IN ('uk_adequacy','eu_uk_adequacy','uk_idta','uk_addendum','eu_scc','none')),
  description text NOT NULL,
  counterparty text,
  valid_until date,
  tra_document_url text,
  verified_by_counsel boolean NOT NULL DEFAULT false,
  UNIQUE (key, version)
);
CREATE VIEW current_transfer_mechanisms AS
  SELECT DISTINCT ON (key) * FROM transfer_mechanisms ORDER BY key, version DESC;
{grant_append_only("transfer_mechanisms")}
GRANT SELECT ON current_transfer_mechanisms TO {APP_ROLE};

CREATE TABLE sub_processors (
  {PK},
  key text NOT NULL,
  {VERSIONED},
  name text NOT NULL,
  purpose text NOT NULL,
  data_categories text[] NOT NULL DEFAULT '{{}}',
  location text NOT NULL,
  transfer_mechanism_key text,
  dpa_url text,
  status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','active','retired')),
  UNIQUE (key, version)
);
{grant_append_only("sub_processors")}

-- ---------------------------------------------------------------- go-live checklist (code can never sign off)
CREATE TABLE golive_checklist_items (
  {PK},
  key text NOT NULL UNIQUE,
  scope text NOT NULL,
  title text NOT NULL,
  description text NOT NULL,
  signed_off_by uuid REFERENCES users(id),
  signed_off_at timestamptz,
  evidence_url text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT signoff_complete CHECK ((signed_off_by IS NULL) = (signed_off_at IS NULL))
);
CREATE INDEX golive_signed_off_by ON golive_checklist_items (signed_off_by);
GRANT SELECT ON golive_checklist_items TO {APP_ROLE};
GRANT INSERT (key, scope, title, description) ON golive_checklist_items TO {APP_ROLE};

-- The ONLY way to sign off an item: an authenticated operator with MFA, via this function.
CREATE FUNCTION golive_sign_off(p_item uuid, p_evidence_url text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE u record;
BEGIN
  SELECT id, role, mfa_enabled, status INTO u FROM users WHERE id = app_user_id();
  IF NOT FOUND OR u.role <> 'operator' OR NOT u.mfa_enabled OR u.status <> 'active' OR NOT app_is_operator() THEN
    RAISE EXCEPTION 'only an active operator with MFA can sign off go-live items' USING ERRCODE = '42501';
  END IF;
  UPDATE golive_checklist_items SET signed_off_by = u.id, signed_off_at = now(), evidence_url = p_evidence_url
   WHERE id = p_item AND signed_off_at IS NULL;
  IF NOT FOUND THEN RAISE EXCEPTION 'item not found or already signed off' USING ERRCODE = 'P0002'; END IF;
  INSERT INTO audit_log (actor_type, actor_id, action, entity_type, entity_id)
  VALUES ('user', u.id::text, 'golive.sign_off', 'golive_checklist_item', p_item::text);
END $$;
REVOKE ALL ON FUNCTION golive_sign_off(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION golive_sign_off(uuid, text) TO {APP_ROLE};

CREATE FUNCTION golive_complete(p_scope text DEFAULT 'uk') RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM golive_checklist_items WHERE scope = p_scope)
     AND NOT EXISTS (SELECT 1 FROM golive_checklist_items WHERE scope = p_scope AND signed_off_at IS NULL)
$$;
GRANT EXECUTE ON FUNCTION golive_complete(text) TO {APP_ROLE};

-- ---------------------------------------------------------------- suppression_list (the ONLY cross-tenant data)
CREATE TABLE suppression_list (
  {PK},
  identifier_hash bytea NOT NULL,            -- HMAC(SUPPRESSION_HASH_KEY, type:normalised); never plaintext
  identifier_type text NOT NULL CHECK (identifier_type IN ('email','domain')),
  scope text NOT NULL CHECK (scope IN ('global','tenant')),
  tenant_id uuid REFERENCES tenants(id),
  reason text NOT NULL CHECK (reason IN ('unsubscribe','objection','erasure','complaint','bounce_hard','manual',
                                         'data_protection_request')),
  source_ref text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT scope_tenant CHECK ((scope = 'tenant') = (tenant_id IS NOT NULL))
);
CREATE UNIQUE INDEX suppression_global_uniq ON suppression_list (identifier_hash) WHERE scope = 'global';
CREATE UNIQUE INDEX suppression_tenant_uniq ON suppression_list (tenant_id, identifier_hash) WHERE scope = 'tenant';
CREATE INDEX suppression_hash ON suppression_list (identifier_hash);
CREATE INDEX suppression_tenant ON suppression_list (tenant_id);
ALTER TABLE suppression_list ENABLE ROW LEVEL SECURITY;
ALTER TABLE suppression_list FORCE ROW LEVEL SECURITY;
CREATE POLICY suppression_visibility ON suppression_list
  USING (scope = 'global' OR tenant_id = app_tenant_id() OR app_is_operator())
  WITH CHECK (scope = 'global' OR tenant_id = app_tenant_id() OR app_is_operator());
{grant_append_only("suppression_list")}
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE IF EXISTS suppression_list CASCADE;
DROP FUNCTION IF EXISTS golive_complete(text), golive_sign_off(uuid, text);
DROP TABLE IF EXISTS golive_checklist_items, sub_processors CASCADE;
DROP VIEW IF EXISTS current_transfer_mechanisms, current_country_rollout, current_compliance_rules;
DROP TABLE IF EXISTS transfer_mechanisms, tax_settings, tax_rules, country_rollout_status, compliance_rules CASCADE;
""")
