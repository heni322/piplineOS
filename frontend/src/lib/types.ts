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
