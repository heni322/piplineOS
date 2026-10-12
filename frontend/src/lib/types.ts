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
