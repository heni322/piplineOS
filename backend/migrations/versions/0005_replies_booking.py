"""Phase 4: message kinds, idempotent reply ingestion, slot offers, reschedule tokens, DP request details.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

from app.db.ddl import APP_ROLE, PK, TID, grant_crud, tenant_rls

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
-- Messages: outreach sequence, AI-drafted replies, templated meeting confirmations and reminders.
ALTER TABLE messages ADD COLUMN kind text NOT NULL DEFAULT 'outreach'
  CHECK (kind IN ('outreach','agent_reply','meeting_confirmation','meeting_reminder'));
ALTER TABLE messages ADD COLUMN reply_id uuid REFERENCES replies(id);
ALTER TABLE messages ADD COLUMN meeting_id uuid REFERENCES meetings(id);
CREATE INDEX messages_reply ON messages (reply_id);
CREATE INDEX messages_meeting ON messages (meeting_id);
DROP INDEX messages_one_send_per_step;
CREATE UNIQUE INDEX messages_one_send_per_step ON messages (lead_id, sequence_step)
  WHERE kind = 'outreach' AND status IN ('sending','sent','dry_run_sent','needs_reconciliation','bounced','complained');

-- Replies: idempotent ingestion by provider message id; threading; handling record (no PII in actions).
ALTER TABLE replies ADD COLUMN provider_message_id text;
ALTER TABLE replies ADD COLUMN in_reply_to text;
ALTER TABLE replies ADD COLUMN subject_enc bytea;
ALTER TABLE replies ADD COLUMN handled_at timestamptz;
ALTER TABLE replies ADD COLUMN actions jsonb NOT NULL DEFAULT '[]';
CREATE UNIQUE INDEX replies_provider_id ON replies (tenant_id, provider_message_id) WHERE provider_message_id IS NOT NULL;

-- Slots offered to a prospect; re-offered if one is taken before acceptance.
CREATE TABLE slot_offers (
  {PK}, {TID},
  lead_id uuid NOT NULL REFERENCES leads(id),
  message_id uuid REFERENCES messages(id),
  slots jsonb NOT NULL,                 -- [{{"starts_at": ISO, "ends_at": ISO, "label": "..."}}]
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','accepted','superseded','expired')),
  accepted_index integer,
  offered_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);
CREATE INDEX slot_offers_lead ON slot_offers (lead_id, offered_at DESC);
CREATE INDEX slot_offers_message ON slot_offers (message_id);

-- Signed reschedule links (same scheme as unsubscribe: signed id, no PII in the URL).
CREATE TABLE reschedule_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), {TID},
  meeting_id uuid NOT NULL REFERENCES meetings(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  used_at timestamptz
);
CREATE INDEX reschedule_tokens_meeting ON reschedule_tokens (meeting_id);
CREATE INDEX reschedule_tokens_tenant ON reschedule_tokens (tenant_id);
CREATE FUNCTION reschedule_lookup(p_id uuid) RETURNS TABLE (tenant_id uuid, meeting_id uuid, used_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT tenant_id, meeting_id, used_at FROM reschedule_tokens WHERE id = p_id
$$;
REVOKE ALL ON FUNCTION reschedule_lookup(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reschedule_lookup(uuid) TO {APP_ROLE};

ALTER TABLE meetings ADD COLUMN prospect_response text CHECK (prospect_response IN ('accepted','declined','tentative'));
ALTER TABLE meetings ADD COLUMN agenda text;
ALTER TABLE meeting_reminders DROP CONSTRAINT meeting_reminders_status_check;
ALTER TABLE meeting_reminders ADD CONSTRAINT meeting_reminders_status_check
  CHECK (status IN ('scheduled','sent','dry_run_sent','cancelled','skipped'));
ALTER TABLE meeting_reminders ADD COLUMN message_id uuid REFERENCES messages(id);
CREATE INDEX reminders_message ON meeting_reminders (message_id);

ALTER TABLE data_protection_requests ADD COLUMN details jsonb NOT NULL DEFAULT '{{}}';
ALTER TABLE data_protection_requests ADD COLUMN completed_at timestamptz;
ALTER TABLE data_protection_requests ADD COLUMN reply_id uuid REFERENCES replies(id);
CREATE INDEX dpr_reply ON data_protection_requests (reply_id);

ALTER TABLE escalations DROP CONSTRAINT escalations_reason_check;
ALTER TABLE escalations ADD CONSTRAINT escalations_reason_check CHECK (reason IN ('anger','legal','pricing','contract',
  'complaint','low_confidence','off_playbook','llm_failure','reschedule_limit','needs_reconciliation','other',
  'data_protection','unsupported_language','calendar_disconnected','prompt_injection'));
""")
    for t in ("slot_offers", "reschedule_tokens"):
        op.execute(tenant_rls(t) + grant_crud(t))
    op.execute("CREATE POLICY definer_lookup ON reschedule_tokens FOR SELECT TO pipelineos_owner USING (true);")
    # Append-only logs that hold encrypted PII must still support erasure/retention: allow ONLY setting the
    # PII columns to NULL; every other change (and every DELETE) stays forbidden.
    op.execute(f"""
CREATE FUNCTION allow_redaction_only() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE col text; o jsonb := to_jsonb(OLD); n jsonb := to_jsonb(NEW);
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  FOR col IN SELECT jsonb_object_keys(o) LOOP
    IF (o -> col) IS DISTINCT FROM (n -> col) AND NOT (col = ANY (TG_ARGV) AND (n -> col) = 'null'::jsonb) THEN
      RAISE EXCEPTION 'table % is append-only (only redaction of % is allowed)', TG_TABLE_NAME, TG_ARGV
        USING ERRCODE = '42501';
    END IF;
  END LOOP;
  RETURN NEW;
END $$;
DROP TRIGGER dry_run_log_append_only ON dry_run_log;
CREATE TRIGGER dry_run_log_append_only BEFORE UPDATE OR DELETE ON dry_run_log
  FOR EACH ROW EXECUTE FUNCTION allow_redaction_only('payload_enc', 'recipient_bidx');
GRANT UPDATE (payload_enc, recipient_bidx) ON dry_run_log TO {APP_ROLE};
DROP TRIGGER llm_calls_append_only ON llm_calls;
CREATE TRIGGER llm_calls_append_only BEFORE UPDATE OR DELETE ON llm_calls
  FOR EACH ROW EXECUTE FUNCTION allow_redaction_only('request_enc', 'response_enc');
GRANT UPDATE (request_enc, response_enc) ON llm_calls TO {APP_ROLE};
""")


def downgrade() -> None:
    op.execute("""
REVOKE UPDATE (request_enc, response_enc) ON llm_calls FROM pipelineos_app;
REVOKE UPDATE (payload_enc, recipient_bidx) ON dry_run_log FROM pipelineos_app;
DROP TRIGGER llm_calls_append_only ON llm_calls;
CREATE TRIGGER llm_calls_append_only BEFORE UPDATE OR DELETE ON llm_calls FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
DROP TRIGGER dry_run_log_append_only ON dry_run_log;
CREATE TRIGGER dry_run_log_append_only BEFORE UPDATE OR DELETE ON dry_run_log
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
DROP FUNCTION allow_redaction_only();
ALTER TABLE escalations DROP CONSTRAINT escalations_reason_check;
ALTER TABLE escalations ADD CONSTRAINT escalations_reason_check CHECK (reason IN ('anger','legal','pricing','contract',
  'complaint','low_confidence','off_playbook','llm_failure','reschedule_limit','needs_reconciliation','other'));
ALTER TABLE data_protection_requests DROP COLUMN reply_id, DROP COLUMN completed_at, DROP COLUMN details;
ALTER TABLE meeting_reminders DROP COLUMN message_id;
ALTER TABLE meeting_reminders DROP CONSTRAINT meeting_reminders_status_check;
ALTER TABLE meeting_reminders ADD CONSTRAINT meeting_reminders_status_check
  CHECK (status IN ('scheduled','sent','dry_run_sent','cancelled'));
ALTER TABLE meetings DROP COLUMN agenda, DROP COLUMN prospect_response;
DROP FUNCTION IF EXISTS reschedule_lookup(uuid);
DROP TABLE IF EXISTS reschedule_tokens, slot_offers;
DROP INDEX IF EXISTS replies_provider_id;
ALTER TABLE replies DROP COLUMN actions, DROP COLUMN handled_at, DROP COLUMN subject_enc, DROP COLUMN in_reply_to,
  DROP COLUMN provider_message_id;
DROP INDEX messages_one_send_per_step;
CREATE UNIQUE INDEX messages_one_send_per_step ON messages (lead_id, sequence_step)
  WHERE status IN ('sending','sent','dry_run_sent','needs_reconciliation','bounced','complained');
ALTER TABLE messages DROP COLUMN meeting_id, DROP COLUMN reply_id, DROP COLUMN kind;
""")
