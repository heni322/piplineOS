"""Core: helper functions, tenancy, identity, keys, sessions, audit and PII access logs.

Revision ID: 0001
Revises:
"""

from alembic import op

from app.db.ddl import APP_ROLE, PK, TID, TS, grant_append_only, grant_crud, tenant_rls, updated_at_trigger

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
GRANT USAGE ON SCHEMA public TO {APP_ROLE};

-- Session context helpers (set per transaction with set_config(..., true)). Missing => NULL => fail closed.
CREATE FUNCTION app_tenant_id() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$;
CREATE FUNCTION app_role() RETURNS text LANGUAGE sql STABLE AS
  $$ SELECT NULLIF(current_setting('app.role', true), '') $$;
CREATE FUNCTION app_user_id() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;
CREATE FUNCTION app_is_operator() RETURNS boolean LANGUAGE sql STABLE AS
  $$ SELECT coalesce(app_role() = 'operator', false) $$;

CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS
  $$ BEGIN NEW.updated_at := now(); RETURN NEW; END $$;

CREATE FUNCTION forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME USING ERRCODE = '42501';
END $$;

-- ---------------------------------------------------------------- tenants
CREATE TABLE tenants (
  {PK},
  slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{{1,62}}$'),
  legal_name text NOT NULL,
  company_number text,
  registered_address text,
  country_code text NOT NULL DEFAULT 'GB',
  vat_number text,
  status text NOT NULL DEFAULT 'onboarding' CHECK (status IN ('onboarding','active','paused','offboarded')),
  role text NOT NULL DEFAULT 'processor_client' CHECK (role IN ('controller','processor_client')),
  is_operator_own boolean NOT NULL DEFAULT false,
  live_mode boolean NOT NULL DEFAULT false,
  approval_mode text NOT NULL DEFAULT 'always' CHECK (approval_mode IN ('always','sampling')),
  approval_sample_pct integer NOT NULL DEFAULT 100 CHECK (approval_sample_pct BETWEEN 0 AND 100),
  approval_mode_locked_until date NOT NULL DEFAULT (current_date + 14),
  billing_currency char(3) NOT NULL DEFAULT 'GBP' CHECK (billing_currency IN ('GBP','EUR')),
  lia_reference text,
  lia_document_url text,
  lia_approved_at timestamptz,
  dpa_status text NOT NULL DEFAULT 'none' CHECK (dpa_status IN ('none','sent','signed')),
  dpa_signed_at timestamptz,
  dpa_version text,
  {TS},
  CONSTRAINT own_tenant_is_controller CHECK (NOT is_operator_own OR role = 'controller'),
  CONSTRAINT dpa_signed_has_date CHECK (dpa_status <> 'signed' OR dpa_signed_at IS NOT NULL)
);
CREATE UNIQUE INDEX tenants_single_operator_own ON tenants (is_operator_own) WHERE is_operator_own;
{updated_at_trigger("tenants")}

-- Approval mode: hybrid model requires human approval of every message for at least the first 2 weeks.
CREATE FUNCTION enforce_approval_lock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.approval_mode = 'sampling' AND current_date < NEW.approval_mode_locked_until THEN
    RAISE EXCEPTION 'approval sampling not allowed before %', NEW.approval_mode_locked_until
      USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.approval_mode_locked_until < OLD.approval_mode_locked_until THEN
    RAISE EXCEPTION 'approval lock cannot be shortened' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tenants_approval_lock BEFORE INSERT OR UPDATE ON tenants
  FOR EACH ROW EXECUTE FUNCTION enforce_approval_lock();

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_self ON tenants
  -- 'system' (scheduler) may list tenants only when NOT acting inside a tenant; inside a tenant it sees itself only
  USING (id = app_tenant_id() OR app_is_operator() OR (app_role() = 'system' AND app_tenant_id() IS NULL))
  WITH CHECK (app_is_operator());
{grant_crud("tenants")}
REVOKE DELETE ON tenants FROM {APP_ROLE};

-- ---------------------------------------------------------------- data keys (ADR-0005)
CREATE TABLE tenant_keys (
  {PK},
  scope_id uuid NOT NULL,            -- tenant id, or the nil UUID for global (non-tenant) PII
  wrapped_key bytea NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  destroyed_at timestamptz
);
CREATE INDEX tenant_keys_scope ON tenant_keys (scope_id, created_at DESC);
GRANT SELECT, INSERT ON tenant_keys TO {APP_ROLE};
GRANT UPDATE (destroyed_at) ON tenant_keys TO {APP_ROLE};

-- ---------------------------------------------------------------- users
CREATE TABLE users (
  {PK},
  tenant_id uuid REFERENCES tenants(id),
  email_bidx bytea NOT NULL UNIQUE,
  email_enc bytea NOT NULL,
  name_enc bytea,
  password_hash text NOT NULL,
  role text NOT NULL CHECK (role IN ('operator','tenant_admin','tenant_viewer')),
  mfa_secret_enc bytea,
  mfa_enabled boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  failed_logins integer NOT NULL DEFAULT 0,
  locked_until timestamptz,
  last_login_at timestamptz,
  {TS},
  CONSTRAINT operator_has_no_tenant CHECK ((role = 'operator') = (tenant_id IS NULL)),
  CONSTRAINT mfa_needs_secret CHECK (NOT mfa_enabled OR mfa_secret_enc IS NOT NULL)
);
CREATE INDEX users_tenant ON users (tenant_id);
{updated_at_trigger("users")}
-- Not FORCEd: the SECURITY DEFINER auth functions below (owned by the owner role) must find users
-- before any tenant context exists. The app role is still fully subject to RLS.
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY users_visibility ON users
  USING (tenant_id = app_tenant_id() OR app_is_operator() OR id = app_user_id())
  WITH CHECK (app_is_operator() OR (tenant_id = app_tenant_id() AND app_role() = 'tenant_admin'));
{grant_crud("users")}
REVOKE DELETE ON users FROM {APP_ROLE};

CREATE FUNCTION auth_find_user(p_bidx bytea)
RETURNS TABLE (id uuid, tenant_id uuid, role text, password_hash text, mfa_enabled boolean,
               mfa_secret_enc bytea, status text, locked_until timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT id, tenant_id, role, password_hash, mfa_enabled, mfa_secret_enc, status, locked_until
  FROM users WHERE email_bidx = p_bidx
$$;
CREATE FUNCTION auth_get_user(p_id uuid)
RETURNS TABLE (id uuid, tenant_id uuid, role text, mfa_enabled boolean, status text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT id, tenant_id, role, mfa_enabled, status FROM users WHERE id = p_id
$$;
CREATE FUNCTION auth_record_login(p_id uuid, p_success boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_success THEN
    UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = now() WHERE id = p_id;
  ELSE
    UPDATE users SET failed_logins = failed_logins + 1,
      locked_until = CASE WHEN failed_logins + 1 >= 5
                          THEN now() + make_interval(mins => least(60, 5 * (failed_logins - 3)))
                          ELSE locked_until END
    WHERE id = p_id;
  END IF;
END $$;
REVOKE ALL ON FUNCTION auth_find_user(bytea), auth_get_user(uuid), auth_record_login(uuid, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_find_user(bytea), auth_get_user(uuid), auth_record_login(uuid, boolean)
  TO {APP_ROLE};

-- ---------------------------------------------------------------- sessions (no PII, token stored hashed)
CREATE TABLE sessions (
  id bytea PRIMARY KEY,                       -- sha256(token)
  user_id uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  idle_expires_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  mfa_verified_at timestamptz,
  ip_country text,
  user_agent_hash text,
  revoked_at timestamptz
);
CREATE INDEX sessions_user ON sessions (user_id);
GRANT SELECT, INSERT, UPDATE ON sessions TO {APP_ROLE};

-- ---------------------------------------------------------------- tenant_configs (versioned)
CREATE TABLE tenant_configs (
  {PK}, {TID},
  version integer NOT NULL CHECK (version > 0),
  schema_version integer NOT NULL DEFAULT 1,
  config jsonb NOT NULL,
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, version)
);
CREATE INDEX tenant_configs_created_by ON tenant_configs (created_by);
{tenant_rls("tenant_configs")}
{grant_append_only("tenant_configs")}

-- ---------------------------------------------------------------- niches (global, no PII)
CREATE TABLE niches (
  {PK},
  slug text NOT NULL UNIQUE,
  name text NOT NULL,
  description text,
  icp_template jsonb NOT NULL DEFAULT '{{}}',
  persona_templates jsonb NOT NULL DEFAULT '[]',
  playbook_template jsonb NOT NULL DEFAULT '{{}}',
  {TS}
);
{updated_at_trigger("niches")}
GRANT SELECT ON niches TO {APP_ROLE};

-- ---------------------------------------------------------------- campaigns
CREATE TABLE campaigns (
  {PK}, {TID},
  niche_id uuid REFERENCES niches(id),
  name text NOT NULL,
  status text NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','active','paused','auto_paused','completed')),
  paused_reason text,
  channel text NOT NULL DEFAULT 'email' CHECK (channel IN ('email')),
  target_countries text[] NOT NULL DEFAULT '{{}}',
  language text NOT NULL DEFAULT 'en-GB',
  sequence_config jsonb NOT NULL DEFAULT '{{}}',
  daily_cap integer NOT NULL DEFAULT 50 CHECK (daily_cap BETWEEN 1 AND 1000),
  started_at timestamptz,
  {TS}
);
CREATE INDEX campaigns_tenant_status ON campaigns (tenant_id, status);
CREATE INDEX campaigns_niche ON campaigns (niche_id);
{updated_at_trigger("campaigns")}
{tenant_rls("campaigns")}
{grant_crud("campaigns")}

-- ---------------------------------------------------------------- audit_log (append-only, partitioned)
CREATE TABLE audit_log (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  tenant_id uuid,
  actor_type text NOT NULL CHECK (actor_type IN ('user','system','agent')),
  actor_id text,
  action text NOT NULL,
  entity_type text,
  entity_id text,
  before jsonb,
  after jsonb,
  correlation_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE TABLE audit_log_default PARTITION OF audit_log DEFAULT;
CREATE INDEX audit_log_created_brin ON audit_log USING brin (created_at);
CREATE INDEX audit_log_entity ON audit_log (tenant_id, entity_type, entity_id);
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_visibility ON audit_log
  USING (tenant_id = app_tenant_id() OR app_is_operator())
  WITH CHECK (tenant_id IS NULL OR tenant_id = app_tenant_id() OR app_is_operator());
{grant_append_only("audit_log")}

-- ---------------------------------------------------------------- pii_access_log (append-only)
CREATE TABLE pii_access_log (
  {PK},
  user_id uuid NOT NULL REFERENCES users(id),
  tenant_id uuid REFERENCES tenants(id),
  lead_id uuid,
  prospect_id uuid,
  action text NOT NULL CHECK (action IN ('unmask','export','view_raw_reply')),
  reason text NOT NULL CHECK (length(trim(reason)) >= 3),
  ip_country text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pii_access_user ON pii_access_log (user_id, created_at DESC);
CREATE INDEX pii_access_tenant ON pii_access_log (tenant_id, created_at DESC);
ALTER TABLE pii_access_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE pii_access_log FORCE ROW LEVEL SECURITY;
CREATE POLICY pii_access_visibility ON pii_access_log
  USING (app_is_operator() OR tenant_id = app_tenant_id())
  WITH CHECK (user_id = app_user_id());
{grant_append_only("pii_access_log")}
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE IF EXISTS pii_access_log, audit_log, campaigns, niches, tenant_configs, sessions CASCADE;
DROP FUNCTION IF EXISTS auth_find_user(bytea), auth_get_user(uuid), auth_record_login(uuid, boolean);
DROP TABLE IF EXISTS users, tenant_keys, tenants CASCADE;
DROP FUNCTION IF EXISTS enforce_approval_lock(), forbid_mutation(), set_updated_at(),
  app_is_operator(), app_user_id(), app_role(), app_tenant_id();
""")
