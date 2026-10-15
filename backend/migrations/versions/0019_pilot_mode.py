"""Business readiness: an emergency stop that HOLDS work, and a pilot contact limit.

- tenants.paused_reason / paused_at: why and when the operator paused a client. A pause holds every queued message
  and every lead in flight; nothing is cancelled or rejected, and resuming continues where it stopped.
- client_pause(reason): the client's OWN emergency stop. A client admin may pause their own account (never another
  client's, never resume: resuming is the operator's deliberate act, after talking to the client).
- campaigns.pilot_max_contacts: the most NEW people a campaign may ever email (first emails only; follow-ups to
  people already contacted continue). Default 100, so every campaign starts as a pilot. NULL = no limit, which only
  an operator can set, and it is audited.

Revision ID: 0019
Revises: 0018
"""

from alembic import op

from app.db.ddl import APP_ROLE

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
ALTER TABLE tenants ADD COLUMN paused_reason text CHECK (char_length(paused_reason) <= 500),
  ADD COLUMN paused_at timestamptz;
ALTER TABLE campaigns ADD COLUMN pilot_max_contacts integer DEFAULT 100
  CHECK (pilot_max_contacts BETWEEN 1 AND 100000);

-- The owner role (migrations and SECURITY DEFINER functions only; the app role never has it) may set its own
-- tenant to 'paused' and nothing else: the only write client_pause needs that the operator-only policy forbids.
CREATE POLICY tenant_self_pause ON tenants FOR UPDATE TO pipelineos_owner
  USING (id = app_tenant_id()) WITH CHECK (id = app_tenant_id() AND status = 'paused');

CREATE FUNCTION client_pause(p_reason text) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE u record; held integer;
BEGIN
  SELECT id, role, tenant_id, status INTO u FROM users WHERE id = app_user_id();
  IF NOT FOUND OR u.role <> 'tenant_admin' OR u.status <> 'active' OR u.tenant_id IS DISTINCT FROM app_tenant_id()
     OR coalesce(btrim(p_reason), '') = '' THEN
    RAISE EXCEPTION 'only an active client admin can pause their own account, with a reason' USING ERRCODE = '42501';
  END IF;
  UPDATE tenants SET status = 'paused', paused_reason = left('Client: ' || btrim(p_reason), 500), paused_at = now()
   WHERE id = u.tenant_id AND status = 'active';
  IF NOT FOUND THEN RAISE EXCEPTION 'account is not active' USING ERRCODE = 'P0002'; END IF;
  SELECT count(*) INTO held FROM messages
   WHERE tenant_id = u.tenant_id AND status IN ('approved','queued','failed_retryable');
  INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, entity_type, entity_id, after)
  VALUES (u.tenant_id, 'user', u.id::text, 'tenant.paused', 'tenant', u.tenant_id::text,
          jsonb_build_object('status', 'paused', 'by', 'client', 'reason', left(btrim(p_reason), 500),
                             'messages_held', held));
  RETURN held;
END $$;
REVOKE ALL ON FUNCTION client_pause(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION client_pause(text) TO {APP_ROLE};
""")


def downgrade() -> None:
    op.execute("""
DROP FUNCTION client_pause(text);
DROP POLICY tenant_self_pause ON tenants;
ALTER TABLE campaigns DROP COLUMN pilot_max_contacts;
ALTER TABLE tenants DROP COLUMN paused_at, DROP COLUMN paused_reason;
""")
