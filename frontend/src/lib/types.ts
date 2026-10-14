export type Claim = { text: string; source_url: string };
export type SequenceMessage = { message_id: string; step: number; subject: string; body: string };
export type ApprovalItem = {
  message_id: string; lead_id: string; tenant_id: string; company: string;
  prospect_name: string; prospect_title: string | null; prospect_country: string | null;
  signal_summary: string | null; signal_url: string | null; relevance_score: number | null;
  subject_options: string[]; sequence: SequenceMessage[]; footer_preview: string; claims: Claim[];
  compliance: { outcome: string; reason_codes: string[]; rules_applied: Record<string, string>; decided_at: string } | null;
  quality_score: number | null; critique: { scores?: Record<string, number>; issues?: string[]; rewrites?: number } | null;
  created_at: string; masked: boolean; kind: string; prospect_reply: string | null;
};
export type ApprovalPage = { items: ApprovalItem[]; total_pending: number };
export type Summary = {
  by_state: Record<string, number>; pending_approval: number; open_escalations: number;
  open_alternative_tasks: number; dry_run_sends: number; real_sends: number; llm_calls: Record<string, number>;
};
export type Escalation = {
  id: string; lead_id: string; company: string; reason: string; detail: Record<string, unknown>;
  pre_escalation_state: string | null; lead_state: string; status: string; created_at: string; allowed_targets: string[];
};
export type DataRequest = {
  id: string; tenant_id: string; lead_id: string | null; company: string | null; request_type: string; regime: string;
  controller: string; status: string; received_at: string; deadline_at: string; days_remaining: number; overdue: boolean;
  response_draft: string | null; completed_at: string | null;
};
export type Meeting = {
  id: string; lead_id: string; company: string; prospect: string | null; starts_at: string; ends_at: string;
  status: string; reschedule_count: number; prospect_response: string | null; lead_state: string;
};
export type InvoiceLine = { id: string; kind: string; description: string; amount_minor: number; meeting_id: string | null;
  evidence: Record<string, unknown>; voided: boolean };
export type Invoice = {
  id: string; tenant_id: string; number: string; status: string; period_start: string | null; period_end: string | null;
  issue_date: string; due_date: string; currency: "GBP" | "EUR"; subtotal_minor: number; tax_minor: number; total_minor: number;
  tax_wording: string | null; blocked_reasons: string[]; supplier: Record<string, string | null>;
  customer: Record<string, string | null>; lines: InvoiceLine[]; disputes_open: number;
};
export type Eligibility = { meeting_id: string; company: string; starts_at: string; status: string; billable: boolean; reasons: string[] };
export type Dispute = { id: string; tenant_id: string; meeting_id: string; company: string; kind: string; reason: string;
  status: string; resolution_note: string | null; created_at: string; window_ends_at: string | null };
export type Tenant = { id: string; slug: string; legal_name: string; status: string; live_mode: boolean; approval_mode: string };
export type Client = { id: string; slug: string; legal_name: string; status: string; billing_currency: string;
  checklist: { key: string; done: boolean; detail: string }[] };
export type Metrics = {
  period: { start: string; end: string }; leads_discovered: number; contacted: number; replied: number; positive_replies: number;
  reply_rate: number | null; positive_reply_rate: number | null; meetings_booked: number; meetings_attended: number;
  no_shows: number; show_rate: number | null; meetings_billed: number; billed_minor: number; ai_calls: number;
  ai_cost_gbp: number | null; ai_cost_per_meeting_gbp: number | null; ai_cost_status: string;
  compliance_blocks: Record<string, number>; escalations: Record<string, number>;
};
export type Terms = { kind: "qualification_criteria" | "pricing"; content: Record<string, unknown> | null; signed: boolean; signed_at: string | null };
export type GovernanceStatus = { global_go_live: boolean; golive_items: number; golive_signed: number; golive_complete: boolean;
  enabled_countries: string[]; live_clients: number; adapter_modes: Record<string, string> };
export type ChecklistItem = { id: string; key: string; scope: string; title: string; description: string; signed_off: boolean; signed_off_at: string | null };
export type CountryRow = { code: string; name: string; stage: string; enabled: boolean; enabled_at: string | null; rule_mode: string | null;
  regime: string | null; verified_by_counsel: boolean; last_reviewed: string | null; source: string | null; can_enable: boolean; blocked_by: string[] };
export type TaxRule = { code: string; version: number; rate_bps: number; invoice_wording: string | null; verified_by_accountant: boolean;
  last_reviewed: string | null; source: string | null };
export type AdequacyRow = { key: string; valid_until: string | null; days_remaining: number | null; warning: boolean };
export type Lead = { id: string; tenant_id: string; state: string; state_changed_at: string; company_name: string | null;
  prospect_name: string | null; prospect_email: string | null; title: string | null; country_code: string | null; masked: boolean };
export type LeadPage = { items: Lead[]; next_cursor: string | null };
export type Unmasked = { lead_id: string; prospect_name: string | null; prospect_email: string | null; profile_url: string | null };
export type Decision = { id: string; lead_id: string; stage: string; outcome: string; reason_codes: string[]; rules_applied: Record<string, string>; created_at: string };
export type Inbox = { id: string; tenant_id: string; tenant_name: string; address: string; status: string; provider: string; daily_cap: number;
  warmup_day: number; sent_today: number; window_days: number; live_sent: number; hard_bounces: number; complaints: number;
  hard_bounce_rate: number | null; complaint_rate: number | null; max_hard_bounce_rate: number; max_complaint_rate: number; min_sent: number;
  last_paused_at: string | null; last_pause_reason: string[] };
export type Campaign = { id: string; tenant_id: string; tenant_name: string; name: string; status: string; daily_cap: number; target_countries: string[] };
export type CrmStatus = "booked" | "confirmed" | "attended" | "no_show" | "cancelled";
export type CrmConnection = { connected: boolean; provider: string | null; pipeline_id: string | null;
  stages: Record<CrmStatus, string> | null; status: string | null; connected_at: string | null; last_sync_at: string | null;
  disconnected_reason: string | null; meetings_synced: number; meetings_waiting: number; meetings_failed: number };
export type SendingDomain = { id: string; tenant_id: string; tenant_name: string; domain: string; status: string;
  spf_ok: boolean; dkim_ok: boolean; dmarc_ok: boolean; dmarc_policy: string | null; issues: string[];
  checked_at: string | null; providers: string[] };
export type ReadinessItem = { key: string; title: string; state: "done" | "todo" | "info"; who: "you" | "system"; detail: string };
