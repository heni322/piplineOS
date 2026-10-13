"""Phase 6.3: the client's own CRM (HubSpot first). One connection per client, with the access token encrypted and
never returned by the API; one sync record per meeting holding only the CRM's object ids (no personal data).

Who may write:
- crm_connections: the client's own admin (from the portal) or the operator; never deleted (disconnect = status).
- crm_links: only the system (the sync worker) or the operator.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

from app.db.ddl import APP_ROLE, PK, TID, TS, tenant_rls, updated_at_trigger

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

WRITERS = "app_role() IN ('operator','system')"


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE crm_connections (
  {PK}, {TID},
  provider text NOT NULL CHECK (provider IN ('hubspot')),
  token_enc bytea NOT NULL,
  pipeline_id text NOT NULL CHECK (length(pipeline_id) BETWEEN 1 AND 100),
  stage_map jsonb NOT NULL,
  status text NOT NULL DEFAULT 'connected' CHECK (status IN ('connected','disconnected')),
  connected_by uuid REFERENCES users(id),
  disconnected_reason text,
  last_sync_at timestamptz,
  {TS},
  CONSTRAINT stage_map_complete CHECK (stage_map ?& ARRAY['booked','confirmed','attended','no_show','cancelled'])
);
CREATE UNIQUE INDEX crm_connections_one_per_tenant ON crm_connections (tenant_id);
CREATE INDEX crm_connections_connected_by ON crm_connections (connected_by);
{updated_at_trigger("crm_connections")}
{tenant_rls("crm_connections")}
CREATE POLICY admin_or_operator_insert ON crm_connections AS RESTRICTIVE FOR INSERT
  WITH CHECK ({WRITERS} OR app_role() = 'tenant_admin');
CREATE POLICY admin_or_operator_update ON crm_connections AS RESTRICTIVE FOR UPDATE
  USING ({WRITERS} OR app_role() = 'tenant_admin') WITH CHECK ({WRITERS} OR app_role() = 'tenant_admin');
GRANT SELECT, INSERT, UPDATE ON crm_connections TO {APP_ROLE};

CREATE TABLE crm_links (
  {PK}, {TID},
  meeting_id uuid NOT NULL UNIQUE REFERENCES meetings(id),
  provider text NOT NULL,
  company_ext_id text,
  contact_ext_id text,
  deal_ext_id text,
  synced_status text,
  synced_mode text CHECK (synced_mode IN ('dry_run','live')),
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','creating','ok','skipped','error')),
  attempts integer NOT NULL DEFAULT 0,
  last_error text,
  synced_at timestamptz,
  {TS}
);
CREATE INDEX crm_links_tenant ON crm_links (tenant_id);
{updated_at_trigger("crm_links")}
{tenant_rls("crm_links")}
CREATE POLICY writers_insert ON crm_links AS RESTRICTIVE FOR INSERT WITH CHECK ({WRITERS});
CREATE POLICY writers_update ON crm_links AS RESTRICTIVE FOR UPDATE USING ({WRITERS}) WITH CHECK ({WRITERS});
GRANT SELECT, INSERT, UPDATE ON crm_links TO {APP_ROLE};
""")


def downgrade() -> None:
    # Refuse rather than delete data: a downgrade only removes these tables while they are empty.
    op.execute("""
DO $$BEGIN
  PERFORM set_config('app.role', 'operator', true);  -- see every tenant's rows (forced row-level security)
  IF EXISTS (SELECT 1 FROM crm_connections) OR EXISTS (SELECT 1 FROM crm_links) THEN
    RAISE EXCEPTION 'crm tables hold data; refusing to drop them (export and clear them deliberately first)';
  END IF;
END$$;
DROP TABLE crm_links;
DROP TABLE crm_connections;
""")
