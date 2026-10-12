export type Claim = { text: string; source_url: string };
export type SequenceMessage = { message_id: string; step: number; subject: string; body: string };
export type ApprovalItem = {
  message_id: string; lead_id: string; tenant_id: string; company: string;
  prospect_name: string; prospect_title: string | null; prospect_country: string | null;
  signal_summary: string | null; signal_url: string | null; relevance_score: number | null;
  subject_options: string[]; sequence: SequenceMessage[]; footer_preview: string; claims: Claim[];
  compliance: { outcome: string; reason_codes: string[]; rules_applied: Record<string, string>; decided_at: string } | null;
  quality_score: number | null; critique: { scores?: Record<string, number>; issues?: string[]; rewrites?: number } | null;
  created_at: string; masked: boolean;
};
export type ApprovalPage = { items: ApprovalItem[]; total_pending: number };
export type Summary = {
  by_state: Record<string, number>; pending_approval: number; open_escalations: number;
  open_alternative_tasks: number; dry_run_sends: number; real_sends: number; llm_calls: Record<string, number>;
};
