"""Domain tables: signals, companies, prospects, leads, decisions, sending, replies, meetings, billing,
DP requests, LLM calls, workflow infrastructure. Includes the database-level send gate.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

from app.db.ddl import APP_ROLE, PK, TID, TS, grant_append_only, grant_crud, tenant_rls, updated_at_trigger

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TENANT_TABLES_CRUD = [
    "companies",
    "signals",
    "prospects",
    "prospect_field_sources",
    "leads",
    "data_protection_requests",
    "alternative_channel_tasks",
    "sending_domains",
    "inboxes",
    "deliverability_metrics",
    "messages",
    "message_personalization_sources",
    "approvals",
    "unsubscribe_tokens",
    "replies",
    "escalations",
    "qualifications",
    "meetings",
    "meeting_reminders",
    "attendance_events",
    "disputes",
    "invoices",
    "invoice_lines",
    "idempotency_keys",
]
TENANT_TABLES_APPEND_ONLY = ["compliance_decisions", "dry_run_log", "llm_calls"]


def upgrade() -> None:
    op.execute(f"""
-- ================================================================ signals & targets
CREATE TABLE companies (
  {PK}, {TID},
  name text,
  name_enc bytea,                        -- used instead of name when the name is personal data (sole traders)
  name_is_personal boolean NOT NULL DEFAULT false,
  domain text,
  domain_normalised text,
  country_code text,
  company_number text,
  legal_form text NOT NULL DEFAULT 'unknown' CHECK (legal_form IN
    ('ltd','plc','llp','lp','partnership','sole_trader','public_body','charity','other_corporate','unknown')),
  legal_form_source text,
  size_band text,
  industry_codes text[] NOT NULL DEFAULT '{{}}',
  embedding vector(1024),
  {TS},
  CONSTRAINT company_has_name CHECK (name IS NOT NULL OR name_enc IS NOT NULL),
  CONSTRAINT personal_name_encrypted CHECK (NOT name_is_personal OR name IS NULL)
);
CREATE UNIQUE INDEX companies_tenant_domain ON companies (tenant_id, domain_normalised) WHERE domain_normalised IS NOT NULL;
CREATE INDEX companies_tenant ON companies (tenant_id);
{updated_at_trigger("companies")}

CREATE TABLE signals (
  {PK}, {TID},
  company_id uuid NOT NULL REFERENCES companies(id),
  campaign_id uuid REFERENCES campaigns(id),
  source_adapter text NOT NULL,
  source_url text NOT NULL,
  signal_type text NOT NULL CHECK (signal_type IN
    ('job_posting','leadership_change','funding','expansion','product_launch','tech_change','tender','other')),
  signal_summary text NOT NULL,
  detected_at timestamptz NOT NULL,
  relevance_score smallint NOT NULL CHECK (relevance_score BETWEEN 0 AND 100),
  reasoning text NOT NULL,
  raw_ref text,
  dedupe_key text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, dedupe_key)
);
CREATE INDEX signals_company ON signals (company_id);
CREATE INDEX signals_campaign ON signals (campaign_id);
CREATE INDEX signals_tenant_detected ON signals (tenant_id, detected_at DESC);

CREATE TABLE prospects (
  {PK}, {TID},
  company_id uuid NOT NULL REFERENCES companies(id),
  name_enc bytea,
  title text,
  email_enc bytea,
  email_bidx bytea,
  profile_url_enc bytea,
  country_code text,                    -- prospect's work location (may differ from company country)
  subdivision_code text,                -- e.g. GB-SCT, for holidays
  subscriber_type text NOT NULL DEFAULT 'unknown' CHECK (subscriber_type IN ('corporate','individual','unknown')),
  subscriber_type_reason text,
  email_verification text NOT NULL DEFAULT 'unknown' CHECK (email_verification IN ('valid','risky','invalid','unknown')),
  email_verified_at timestamptz,
  role_relevance numeric(3,2) CHECK (role_relevance BETWEEN 0 AND 1),
  confidence_score numeric(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
  collected_at timestamptz NOT NULL DEFAULT now(),
  anonymised_at timestamptz,
  {TS},
  CONSTRAINT anonymised_has_no_pii CHECK (anonymised_at IS NULL OR
    (name_enc IS NULL AND email_enc IS NULL AND email_bidx IS NULL AND profile_url_enc IS NULL))
);
CREATE UNIQUE INDEX prospects_tenant_email ON prospects (tenant_id, email_bidx) WHERE email_bidx IS NOT NULL;
CREATE INDEX prospects_company ON prospects (company_id);
CREATE INDEX prospects_tenant ON prospects (tenant_id);
{updated_at_trigger("prospects")}

CREATE TABLE prospect_field_sources (
  {PK}, {TID},
  prospect_id uuid NOT NULL REFERENCES prospects(id),
  field_name text NOT NULL,
  data_source text NOT NULL,
  source_url text,
  collected_at timestamptz NOT NULL DEFAULT now(),
  lawful_basis_note text,
  UNIQUE (prospect_id, field_name, data_source)
);
CREATE INDEX pfs_tenant ON prospect_field_sources (tenant_id);

CREATE TABLE leads (
  {PK}, {TID},
  campaign_id uuid NOT NULL REFERENCES campaigns(id),
  company_id uuid NOT NULL REFERENCES companies(id),
  prospect_id uuid REFERENCES prospects(id),
  signal_id uuid REFERENCES signals(id),
  referred_from_lead_id uuid REFERENCES leads(id),
  state text NOT NULL DEFAULT 'discovered' CHECK (state IN (
    'discovered','enriched','compliance_checked','pending_approval','approved','contacted','replied',
    'qualified','booked','confirmed','attended','billed',
    'rejected_by_compliance','alternative_channel','suppressed','objected','erased','disqualified',
    'rescheduled','no_show','disputed','escalated')),
  state_changed_at timestamptz NOT NULL DEFAULT now(),
  pre_escalation_state text,
  version integer NOT NULL DEFAULT 1,
  paused_until timestamptz,
  objected_after_booking boolean NOT NULL DEFAULT false,
  correlation_id text NOT NULL DEFAULT gen_random_uuid()::text,
  {TS}
);
CREATE UNIQUE INDEX leads_campaign_prospect ON leads (campaign_id, prospect_id) WHERE prospect_id IS NOT NULL;
CREATE INDEX leads_tenant_state ON leads (tenant_id, state, state_changed_at DESC);
CREATE INDEX leads_company ON leads (company_id);
CREATE INDEX leads_prospect ON leads (prospect_id);
CREATE INDEX leads_signal ON leads (signal_id);
CREATE INDEX leads_referred_from ON leads (referred_from_lead_id);
CREATE INDEX leads_paused ON leads (paused_until) WHERE paused_until IS NOT NULL;
{updated_at_trigger("leads")}

-- ================================================================ compliance decisions & DP requests
CREATE TABLE compliance_decisions (
  {PK}, {TID},
  lead_id uuid NOT NULL REFERENCES leads(id),
  message_id uuid,
  stage text NOT NULL CHECK (stage IN ('queue','pre_approval','send','referral','recheck')),
  outcome text NOT NULL CHECK (outcome IN ('allow','block','alternative_channel')),
  reason_codes text[] NOT NULL,
  rules_applied jsonb NOT NULL,
  effective_country_code text,
  suppression_checked_at timestamptz NOT NULL,
  engine_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX compliance_decisions_lead ON compliance_decisions (lead_id, created_at DESC);
CREATE INDEX compliance_decisions_message ON compliance_decisions (message_id);
CREATE INDEX compliance_decisions_tenant ON compliance_decisions (tenant_id, created_at DESC);

CREATE TABLE data_protection_requests (
  {PK}, {TID},
  lead_id uuid REFERENCES leads(id),
  prospect_ref_hash bytea NOT NULL,
  request_type text NOT NULL CHECK (request_type IN
    ('access','erasure','objection','rectification','restriction','portability','unsubscribe','unclear')),
  regime text NOT NULL CHECK (regime IN ('uk_gdpr','eu_gdpr','both')),
  controller text NOT NULL CHECK (controller IN ('tenant','operator')),
  received_at timestamptz NOT NULL,
  deadline_at timestamptz NOT NULL,
  extended_deadline_at timestamptz,
  status text NOT NULL DEFAULT 'open' CHECK (status IN
    ('open','in_progress','awaiting_tenant','completed','refused_with_reason')),
  response_draft_enc bytea,
  responded_at timestamptz,
  handled_by uuid REFERENCES users(id),
  {TS}
);
CREATE INDEX dpr_status_deadline ON data_protection_requests (tenant_id, status, deadline_at);
CREATE INDEX dpr_lead ON data_protection_requests (lead_id);
CREATE INDEX dpr_handled_by ON data_protection_requests (handled_by);
{updated_at_trigger("data_protection_requests")}

CREATE TABLE alternative_channel_tasks (
  {PK}, {TID},
  lead_id uuid NOT NULL REFERENCES leads(id),
  channel text NOT NULL CHECK (channel IN ('linkedin_manual','warm_intro','event','inbound_content')),
  instructions text NOT NULL,
  assigned_to uuid REFERENCES users(id),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','skipped')),
  due_at timestamptz,
  {TS}
);
CREATE INDEX act_lead ON alternative_channel_tasks (lead_id);
CREATE INDEX act_assigned ON alternative_channel_tasks (assigned_to);
{updated_at_trigger("alternative_channel_tasks")}

-- ================================================================ sending & deliverability
CREATE TABLE sending_domains (
  {PK}, {TID},
  domain text NOT NULL UNIQUE,
  is_tenant_primary boolean NOT NULL DEFAULT false CHECK (NOT is_tenant_primary),
  spf_ok boolean NOT NULL DEFAULT false,
  dkim_ok boolean NOT NULL DEFAULT false,
  dmarc_ok boolean NOT NULL DEFAULT false,
  dmarc_policy text,
  verified_at timestamptz,
  warmup_stage integer NOT NULL DEFAULT 0,
  health_score numeric(5,2),
  status text NOT NULL DEFAULT 'setup' CHECK (status IN ('setup','warming','active','paused')),
  {TS}
);
CREATE INDEX sending_domains_tenant ON sending_domains (tenant_id);
{updated_at_trigger("sending_domains")}

CREATE TABLE inboxes (
  {PK}, {TID},
  sending_domain_id uuid NOT NULL REFERENCES sending_domains(id),
  address text NOT NULL UNIQUE,
  display_name text NOT NULL,
  provider text NOT NULL DEFAULT 'mock',
  oauth_token_enc bytea,
  oauth_status text NOT NULL DEFAULT 'ok' CHECK (oauth_status IN ('ok','expired','revoked','not_connected')),
  daily_cap integer NOT NULL DEFAULT 30 CHECK (daily_cap BETWEEN 1 AND 50),
  warmup_day integer NOT NULL DEFAULT 0,
  health_score numeric(5,2),
  status text NOT NULL DEFAULT 'warming' CHECK (status IN ('warming','active','paused')),
  {TS}
);
CREATE INDEX inboxes_tenant ON inboxes (tenant_id);
CREATE INDEX inboxes_domain ON inboxes (sending_domain_id);
{updated_at_trigger("inboxes")}

CREATE TABLE deliverability_metrics (
  {PK}, {TID},
  inbox_id uuid REFERENCES inboxes(id),
  sending_domain_id uuid REFERENCES sending_domains(id),
  campaign_id uuid REFERENCES campaigns(id),
  date date NOT NULL,
  sent integer NOT NULL DEFAULT 0, delivered integer NOT NULL DEFAULT 0,
  bounced_hard integer NOT NULL DEFAULT 0, bounced_soft integer NOT NULL DEFAULT 0,
  complaints integer NOT NULL DEFAULT 0, replies integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (inbox_id, date)
);
CREATE INDEX dm_domain ON deliverability_metrics (sending_domain_id);
CREATE INDEX dm_campaign ON deliverability_metrics (campaign_id);
CREATE INDEX dm_tenant_date ON deliverability_metrics (tenant_id, date);

CREATE TABLE messages (
  {PK}, {TID},
  lead_id uuid NOT NULL REFERENCES leads(id),
  campaign_id uuid NOT NULL REFERENCES campaigns(id),
  inbox_id uuid REFERENCES inboxes(id),
  sequence_step smallint NOT NULL DEFAULT 0 CHECK (sequence_step >= 0),
  language text NOT NULL DEFAULT 'en-GB',
  subject_enc bytea,
  body_enc bytea,
  compliance_footer text,
  quality_score numeric(3,1) CHECK (quality_score BETWEEN 0 AND 10),
  critique jsonb,
  prompt_version text,
  recipient_hash bytea NOT NULL,          -- suppression-key hash of recipient email (send gate checks it)
  recipient_domain_hash bytea NOT NULL,   -- suppression-key hash of recipient domain
  status text NOT NULL DEFAULT 'draft' CHECK (status IN (
    'draft','pending_approval','approved','queued','sending','sent','dry_run_sent','failed_retryable',
    'failed_permanent','needs_reconciliation','blocked_at_send','bounced','complained','cancelled')),
  idempotency_key text NOT NULL UNIQUE,
  send_compliance_decision_id uuid REFERENCES compliance_decisions(id),
  suppression_checked_at timestamptz,
  scheduled_for timestamptz,
  sending_started_at timestamptz,
  sent_at timestamptz,
  provider_message_id text,
  mode_at_send text CHECK (mode_at_send IN ('dry_run','sandbox','live')),
  last_error text,
  {TS}
);
-- No duplicate sends: at most one in-flight/sent message per lead and sequence step.
CREATE UNIQUE INDEX messages_one_send_per_step ON messages (lead_id, sequence_step)
  WHERE status IN ('sending','sent','dry_run_sent','needs_reconciliation','bounced','complained');
CREATE INDEX messages_lead ON messages (lead_id);
CREATE INDEX messages_campaign ON messages (campaign_id);
CREATE INDEX messages_inbox_sent ON messages (inbox_id, sent_at);
CREATE INDEX messages_decision ON messages (send_compliance_decision_id);
CREATE INDEX messages_status_sched ON messages (tenant_id, status, scheduled_for);
{updated_at_trigger("messages")}
ALTER TABLE compliance_decisions ADD CONSTRAINT compliance_decisions_message_fk
  FOREIGN KEY (message_id) REFERENCES messages(id);

CREATE TABLE message_personalization_sources (
  {PK}, {TID},
  message_id uuid NOT NULL REFERENCES messages(id),
  claim_text text NOT NULL,
  source_url text NOT NULL,
  source_type text,
  retrieved_at timestamptz,
  source_snapshot_hash text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mps_message ON message_personalization_sources (message_id);
CREATE INDEX mps_tenant ON message_personalization_sources (tenant_id);

CREATE TABLE approvals (
  {PK}, {TID},
  message_id uuid NOT NULL REFERENCES messages(id),
  reviewer_id uuid NOT NULL REFERENCES users(id),
  decision text NOT NULL CHECK (decision IN ('approved','edited','rejected')),
  original_body_enc bytea,
  edited_body_enc bytea,
  diff text,
  reason text,
  review_seconds integer,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT rejection_has_reason CHECK (decision <> 'rejected' OR reason IS NOT NULL)
);
CREATE INDEX approvals_message ON approvals (message_id);
CREATE INDEX approvals_reviewer ON approvals (reviewer_id);
CREATE INDEX approvals_tenant ON approvals (tenant_id, created_at DESC);

CREATE TABLE dry_run_log (
  {PK}, {TID},
  effect_type text NOT NULL CHECK (effect_type IN
    ('email_send','calendar_create','calendar_update','calendar_cancel','crm_write','paid_api_call')),
  adapter text NOT NULL,
  idempotency_key text NOT NULL,
  entity_id uuid,
  payload_enc bytea,
  recipient_bidx bytea,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, effect_type, idempotency_key)
);
CREATE INDEX dry_run_tenant ON dry_run_log (tenant_id, created_at DESC);

CREATE TABLE unsubscribe_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), {TID},
  message_id uuid REFERENCES messages(id),
  lead_id uuid NOT NULL REFERENCES leads(id),
  recipient_hash bytea NOT NULL,
  recipient_domain_hash bytea NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  used_at timestamptz
);
CREATE INDEX unsub_message ON unsubscribe_tokens (message_id);
CREATE INDEX unsub_lead ON unsubscribe_tokens (lead_id);
CREATE INDEX unsub_tenant ON unsubscribe_tokens (tenant_id);

-- Public unsubscribe happens without a session: resolve the tenant from the (signature-verified) token id.
CREATE FUNCTION unsubscribe_lookup(p_id uuid) RETURNS TABLE (tenant_id uuid, lead_id uuid, used_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT tenant_id, lead_id, used_at FROM unsubscribe_tokens WHERE id = p_id
$$;
REVOKE ALL ON FUNCTION unsubscribe_lookup(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION unsubscribe_lookup(uuid) TO {APP_ROLE};

-- ================================================================ replies, qualification, meetings
CREATE TABLE replies (
  {PK}, {TID},
  lead_id uuid NOT NULL REFERENCES leads(id),
  message_id uuid REFERENCES messages(id),
  received_at timestamptz NOT NULL,
  from_bidx bytea,
  raw_enc bytea,
  body_text_enc bytea,
  detected_language text,
  classification text CHECK (classification IN ('interested','objection','not_now','referral','out_of_office',
    'unsubscribe','data_protection_request','negative','unclear')),
  confidence numeric(3,2) CHECK (confidence BETWEEN 0 AND 1),
  rule_flags text[] NOT NULL DEFAULT '{{}}',
  ooo_return_date date,
  extracted_referral jsonb,
  classified_at timestamptz,
  prompt_version text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX replies_lead ON replies (lead_id);
CREATE INDEX replies_message ON replies (message_id);
CREATE INDEX replies_tenant_received ON replies (tenant_id, received_at DESC);
CREATE INDEX replies_unclassified ON replies (received_at) WHERE classified_at IS NULL;

CREATE TABLE escalations (
  {PK}, {TID},
  lead_id uuid NOT NULL REFERENCES leads(id),
  reason text NOT NULL CHECK (reason IN ('anger','legal','pricing','contract','complaint','low_confidence',
    'off_playbook','llm_failure','reschedule_limit','needs_reconciliation','other')),
  context jsonb NOT NULL DEFAULT '{{}}',
  pre_escalation_state text,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','resolved')),
  assigned_to uuid REFERENCES users(id),
  resolved_at timestamptz,
  resolution text,
  {TS}
);
CREATE INDEX escalations_lead ON escalations (lead_id);
CREATE INDEX escalations_assigned ON escalations (assigned_to);
CREATE INDEX escalations_open ON escalations (tenant_id, status, created_at);
{updated_at_trigger("escalations")}

CREATE TABLE qualifications (
  {PK}, {TID},
  lead_id uuid NOT NULL UNIQUE REFERENCES leads(id),
  framework text NOT NULL CHECK (framework IN ('bant','meddic','custom')),
  criteria_config_id uuid NOT NULL REFERENCES tenant_configs(id),
  evidence jsonb NOT NULL,
  overall_met boolean NOT NULL,
  confidence numeric(3,2) CHECK (confidence BETWEEN 0 AND 1),
  decided_by text NOT NULL CHECK (decided_by IN ('agent','human')),
  {TS}
);
CREATE INDEX qualifications_config ON qualifications (criteria_config_id);
CREATE INDEX qualifications_tenant ON qualifications (tenant_id);
{updated_at_trigger("qualifications")}

CREATE TABLE meetings (
  {PK}, {TID},
  lead_id uuid NOT NULL REFERENCES leads(id),
  calendar_provider text NOT NULL,
  provider_event_id text,
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL,
  prospect_tz text NOT NULL,
  host_tz text NOT NULL,
  status text NOT NULL DEFAULT 'proposed' CHECK (status IN
    ('proposed','booked','confirmed','rescheduled','attended','no_show','cancelled')),
  booked_by_system boolean NOT NULL DEFAULT true,
  reschedule_count integer NOT NULL DEFAULT 0,
  brief_enc bytea,
  idempotency_key text NOT NULL UNIQUE,
  {TS},
  CONSTRAINT meeting_times CHECK (ends_at > starts_at)
);
CREATE UNIQUE INDEX meetings_one_active_per_lead ON meetings (lead_id) WHERE status IN ('booked','confirmed');
CREATE INDEX meetings_lead ON meetings (lead_id);
CREATE INDEX meetings_tenant_starts ON meetings (tenant_id, starts_at);
{updated_at_trigger("meetings")}

CREATE TABLE meeting_reminders (
  {PK}, {TID},
  meeting_id uuid NOT NULL REFERENCES meetings(id),
  offset_minutes integer NOT NULL CHECK (offset_minutes > 0),
  channel text NOT NULL DEFAULT 'email',
  scheduled_for timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled','sent','dry_run_sent','cancelled')),
  idempotency_key text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX reminders_meeting ON meeting_reminders (meeting_id);
CREATE INDEX reminders_due ON meeting_reminders (status, scheduled_for);

CREATE TABLE attendance_events (
  {PK}, {TID},
  meeting_id uuid NOT NULL REFERENCES meetings(id),
  source text NOT NULL CHECK (source IN ('calendar','crm','tenant_confirmation','operator')),
  status text NOT NULL CHECK (status IN ('attended','no_show','cancelled')),
  recorded_by uuid REFERENCES users(id),
  recorded_at timestamptz NOT NULL DEFAULT now(),
  evidence jsonb NOT NULL DEFAULT '{{}}'
);
CREATE INDEX attendance_meeting ON attendance_events (meeting_id);
CREATE INDEX attendance_recorded_by ON attendance_events (recorded_by);

-- ================================================================ billing
CREATE TABLE invoices (
  {PK}, {TID},
  number text NOT NULL UNIQUE,
  issue_date date NOT NULL,
  due_date date NOT NULL,
  currency char(3) NOT NULL CHECK (currency IN ('GBP','EUR')),
  supplier_snapshot jsonb NOT NULL,
  customer_snapshot jsonb NOT NULL,
  place_of_supply text NOT NULL,
  tax_rule_id uuid REFERENCES tax_rules(id),
  tax_wording text,
  subtotal_minor bigint NOT NULL,
  tax_minor bigint NOT NULL,
  total_minor bigint NOT NULL,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','issued','paid','void')),
  {TS},
  CONSTRAINT invoice_total CHECK (total_minor = subtotal_minor + tax_minor)
);
CREATE INDEX invoices_tenant ON invoices (tenant_id, issue_date DESC);
CREATE INDEX invoices_tax_rule ON invoices (tax_rule_id);
{updated_at_trigger("invoices")}

CREATE TABLE invoice_lines (
  {PK}, {TID},
  invoice_id uuid NOT NULL REFERENCES invoices(id),
  kind text NOT NULL CHECK (kind IN ('setup','retainer','meeting','credit')),
  meeting_id uuid UNIQUE REFERENCES meetings(id),
  description text NOT NULL,
  amount_minor bigint NOT NULL,
  evidence jsonb NOT NULL DEFAULT '{{}}',
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT meeting_line_has_meeting CHECK (kind <> 'meeting' OR meeting_id IS NOT NULL)
);
CREATE INDEX invoice_lines_invoice ON invoice_lines (invoice_id);

CREATE TABLE disputes (
  {PK}, {TID},
  meeting_id uuid NOT NULL REFERENCES meetings(id),
  invoice_line_id uuid REFERENCES invoice_lines(id),
  raised_by uuid NOT NULL REFERENCES users(id),
  reason text NOT NULL CHECK (length(trim(reason)) >= 3),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','upheld','rejected')),
  resolution_note text,
  resolved_by uuid REFERENCES users(id),
  resolved_at timestamptz,
  {TS}
);
CREATE INDEX disputes_meeting ON disputes (meeting_id);
CREATE INDEX disputes_line ON disputes (invoice_line_id);
CREATE INDEX disputes_raised_by ON disputes (raised_by);
CREATE INDEX disputes_resolved_by ON disputes (resolved_by);
{updated_at_trigger("disputes")}

-- ================================================================ LLM calls (append-only)
CREATE TABLE llm_calls (
  {PK}, {TID},
  lead_id uuid REFERENCES leads(id),
  agent text NOT NULL,
  prompt_id text NOT NULL,
  prompt_version integer NOT NULL,
  prompt_hash text NOT NULL,
  model text NOT NULL,
  input_tokens integer NOT NULL DEFAULT 0,
  output_tokens integer NOT NULL DEFAULT 0,
  cost_micros_gbp bigint,                 -- NULL when no verified price is configured
  latency_ms integer,
  attempt smallint NOT NULL,
  outcome text NOT NULL CHECK (outcome IN ('ok','invalid_retry','refusal','timeout','error','escalated','budget_exceeded')),
  validation_errors jsonb,
  request_enc bytea,
  response_enc bytea,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_calls_tenant ON llm_calls (tenant_id, created_at);
CREATE INDEX llm_calls_lead ON llm_calls (lead_id);

-- ================================================================ workflow infrastructure
-- outbox and scheduled_actions hold IDs only (no PII) and are processed by system jobs across tenants.
CREATE TABLE outbox (
  {PK},
  tenant_id uuid REFERENCES tenants(id),
  topic text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  dispatched_at timestamptz,
  attempts integer NOT NULL DEFAULT 0
);
CREATE INDEX outbox_pending ON outbox (created_at) WHERE dispatched_at IS NULL;
CREATE INDEX outbox_tenant ON outbox (tenant_id);
GRANT SELECT, INSERT, UPDATE ON outbox TO {APP_ROLE};

CREATE TABLE scheduled_actions (
  {PK},
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  lead_id uuid REFERENCES leads(id),
  action text NOT NULL,
  run_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','done','cancelled','failed')),
  idempotency_key text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX scheduled_due ON scheduled_actions (status, run_at);
CREATE INDEX scheduled_tenant ON scheduled_actions (tenant_id);
CREATE INDEX scheduled_lead ON scheduled_actions (lead_id);
GRANT SELECT, INSERT, UPDATE ON scheduled_actions TO {APP_ROLE};

CREATE TABLE idempotency_keys (
  key text NOT NULL,
  {TID},
  scope text NOT NULL,
  request_hash text NOT NULL,
  status_code integer,
  response jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL DEFAULT now() + interval '7 days',
  PRIMARY KEY (tenant_id, scope, key)
);
""")

    # RLS + grants for every tenant-owned table
    for t in TENANT_TABLES_CRUD:
        op.execute(tenant_rls(t) + grant_crud(t))
    for t in TENANT_TABLES_APPEND_ONLY:
        op.execute(tenant_rls(t) + grant_append_only(t))
    op.execute(f"REVOKE DELETE ON leads, messages, prospects FROM {APP_ROLE};")
    # FORCE RLS also binds the table owner. The owner role is used only by migrations and SECURITY DEFINER
    # functions; unsubscribe_lookup() must resolve a signature-verified token before any tenant context exists.
    op.execute("CREATE POLICY definer_lookup ON unsubscribe_tokens FOR SELECT TO pipelineos_owner USING (true);")

    op.execute("""
-- ================================================================ the database-level SEND GATE (ADR-0004)
CREATE FUNCTION enforce_send_gate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  d record;
  max_age interval := make_interval(mins =>
    coalesce(nullif(current_setting('pipelineos.send_decision_max_age_minutes', true), '')::int, 15));
BEGIN
  IF TG_OP = 'INSERT' AND NEW.status IN ('sending','sent','dry_run_sent') THEN
    RAISE EXCEPTION 'send gate: a message cannot be created in status %', NEW.status USING ERRCODE = 'P0001';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IN ('sent','dry_run_sent','bounced','complained')
     AND NEW.status NOT IN ('sent','dry_run_sent','bounced','complained') THEN
    RAISE EXCEPTION 'send gate: a sent message cannot go back to %', NEW.status USING ERRCODE = 'P0001';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.status IN ('sent','dry_run_sent') AND OLD.status IS DISTINCT FROM NEW.status THEN
    IF OLD.status <> 'sending' THEN
      RAISE EXCEPTION 'send gate: message must pass through sending' USING ERRCODE = 'P0001';
    END IF;
    SELECT * INTO d FROM compliance_decisions WHERE id = NEW.send_compliance_decision_id;
    IF NOT FOUND OR d.outcome <> 'allow' OR d.stage <> 'send' OR d.lead_id <> NEW.lead_id
       OR d.message_id IS DISTINCT FROM NEW.id OR d.created_at < now() - max_age THEN
      RAISE EXCEPTION 'send gate: no fresh send-time allow decision for this message' USING ERRCODE = 'P0001';
    END IF;
    IF NEW.suppression_checked_at IS NULL OR NEW.suppression_checked_at < now() - max_age THEN
      RAISE EXCEPTION 'send gate: suppression not checked recently' USING ERRCODE = 'P0001';
    END IF;
    IF EXISTS (SELECT 1 FROM suppression_list s
               WHERE s.identifier_hash IN (NEW.recipient_hash, NEW.recipient_domain_hash)
                 AND (s.scope = 'global' OR s.tenant_id = NEW.tenant_id)) THEN
      RAISE EXCEPTION 'send gate: recipient is suppressed' USING ERRCODE = 'P0001';
    END IF;
    IF NEW.status = 'dry_run_sent' AND NEW.mode_at_send IS DISTINCT FROM 'dry_run' THEN
      RAISE EXCEPTION 'send gate: dry_run_sent requires mode dry_run' USING ERRCODE = 'P0001';
    END IF;
    IF NEW.status = 'sent' AND NEW.mode_at_send = 'live' THEN
      IF NOT coalesce((SELECT live_mode FROM tenants WHERE id = NEW.tenant_id), false) THEN
        RAISE EXCEPTION 'send gate: tenant is not in live mode' USING ERRCODE = 'P0001';
      END IF;
      IF NOT golive_complete('uk') THEN
        RAISE EXCEPTION 'send gate: go-live checklist not fully signed off' USING ERRCODE = 'P0001';
      END IF;
    ELSIF NEW.status = 'sent' AND NEW.mode_at_send IS DISTINCT FROM 'sandbox' THEN
      RAISE EXCEPTION 'send gate: sent requires mode live or sandbox' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER messages_send_gate BEFORE INSERT OR UPDATE ON messages
  FOR EACH ROW EXECUTE FUNCTION enforce_send_gate();

-- A lead may only become 'contacted' if a message for it actually passed the send gate.
CREATE FUNCTION enforce_lead_contacted() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.state = 'contacted' AND OLD.state IS DISTINCT FROM 'contacted' AND OLD.state <> 'replied' THEN
    IF NOT EXISTS (SELECT 1 FROM messages m WHERE m.lead_id = NEW.id AND m.status IN ('sent','dry_run_sent')) THEN
      RAISE EXCEPTION 'lead gate: contacted requires a message that passed the send gate' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  IF OLD.state = 'erased' AND NEW.state <> 'erased' THEN
    RAISE EXCEPTION 'lead gate: erased is absorbing' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER leads_contacted_gate BEFORE UPDATE ON leads
  FOR EACH ROW EXECUTE FUNCTION enforce_lead_contacted();
""")


def downgrade() -> None:
    op.execute("""
DROP TRIGGER IF EXISTS leads_contacted_gate ON leads;
DROP TRIGGER IF EXISTS messages_send_gate ON messages;
DROP FUNCTION IF EXISTS enforce_lead_contacted(), enforce_send_gate(), unsubscribe_lookup(uuid);
DROP TABLE IF EXISTS idempotency_keys, scheduled_actions, outbox, llm_calls, disputes, invoice_lines, invoices,
  attendance_events, meeting_reminders, meetings, qualifications, escalations, replies, unsubscribe_tokens,
  dry_run_log, approvals, message_personalization_sources, compliance_decisions, messages,
  deliverability_metrics, inboxes, sending_domains, alternative_channel_tasks, data_protection_requests,
  leads, prospect_field_sources, prospects, signals, companies CASCADE;
""")
