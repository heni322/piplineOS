"""Phase 6: single-use account tokens (client invites, password resets). Only a hash of each token is stored;
setting a password goes through a SECURITY DEFINER function so no anonymous context can touch users directly.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

from app.db.ddl import APP_ROLE

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE account_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id),
  purpose text NOT NULL CHECK (purpose IN ('invite','reset')),
  token_hash bytea NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX account_tokens_user ON account_tokens (user_id);
CREATE INDEX account_tokens_created_by ON account_tokens (created_by);
ALTER TABLE account_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_tokens FORCE ROW LEVEL SECURITY;
CREATE POLICY operator_only ON account_tokens USING (app_is_operator()) WITH CHECK (app_is_operator());
CREATE POLICY definer_access ON account_tokens TO pipelineos_owner USING (true) WITH CHECK (true);
GRANT SELECT, INSERT ON account_tokens TO {APP_ROLE};

-- Anyone holding a valid, unused, unexpired token may set that one account's password, once.
CREATE FUNCTION auth_set_password(p_hash bytea, p_password_hash text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE t record;
BEGIN
  SELECT * INTO t FROM account_tokens WHERE token_hash = p_hash FOR UPDATE;
  IF NOT FOUND OR t.used_at IS NOT NULL OR t.expires_at < now() THEN
    RETURN NULL;
  END IF;
  UPDATE account_tokens SET used_at = now() WHERE id = t.id;
  -- one reset invalidates every other open token of the same account
  UPDATE account_tokens SET used_at = now() WHERE user_id = t.user_id AND used_at IS NULL;
  UPDATE users SET password_hash = p_password_hash, failed_logins = 0, locked_until = NULL WHERE id = t.user_id;
  DELETE FROM sessions WHERE user_id = t.user_id;  -- sign out everywhere after a password change
  INSERT INTO audit_log (actor_type, actor_id, action, entity_type, entity_id)
  VALUES ('user', t.user_id::text, 'auth.password_set.' || t.purpose, 'user', t.user_id::text);
  RETURN t.user_id;
END $$;

-- Password reset request: issues a token for an existing active account (caller never learns whether it exists).
CREATE FUNCTION auth_issue_reset(p_bidx bytea, p_hash bytea, p_expires timestamptz)
RETURNS TABLE (user_id uuid, tenant_id uuid, email_enc bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE u record;
BEGIN
  SELECT id, users.tenant_id, users.email_enc INTO u FROM users WHERE email_bidx = p_bidx AND status = 'active';
  IF NOT FOUND THEN RETURN; END IF;
  IF (SELECT count(*) FROM account_tokens WHERE account_tokens.user_id = u.id AND purpose = 'reset'
      AND created_at > now() - interval '1 hour') >= 3 THEN
    RETURN;  -- at most 3 reset emails per account per hour
  END IF;
  INSERT INTO account_tokens (user_id, purpose, token_hash, expires_at) VALUES (u.id, 'reset', p_hash, p_expires);
  RETURN QUERY SELECT u.id, u.tenant_id, u.email_enc;
END $$;
REVOKE ALL ON FUNCTION auth_set_password(bytea, text), auth_issue_reset(bytea, bytea, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_set_password(bytea, text), auth_issue_reset(bytea, bytea, timestamptz) TO {APP_ROLE};
-- readiness probe: the app may read which schema revision it is running against (no data in this table)
GRANT SELECT ON alembic_version TO {APP_ROLE};
""")


def downgrade() -> None:
    op.execute("""
REVOKE SELECT ON alembic_version FROM pipelineos_app;
DROP FUNCTION IF EXISTS auth_issue_reset(bytea, bytea, timestamptz);
DROP FUNCTION IF EXISTS auth_set_password(bytea, text);
DROP TABLE IF EXISTS account_tokens;
""")
