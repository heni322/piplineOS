# 04 — Lead State Machine

Status: **Proposed (Phase 1)**

The lead state machine is plain Python (`backend/app/domain/leads/state_machine.py`): a static transition table, guards, and effects. It is the single place that changes `leads.state`. Every transition writes `audit_log` (from, to, actor, reason, correlation_id) in the same transaction, and uses optimistic locking (`UPDATE … WHERE id = :id AND version = :v`).

## 1. States

**Main path**

```
discovered → enriched → compliance_checked → pending_approval → approved → contacted
→ replied → qualified → booked → confirmed → attended → billed
```

**Side / terminal states**

| State | Terminal? | Meaning |
|---|---|---|
| `rejected_by_compliance` | Terminal for this lead (rules may change → re-evaluation creates a new decision, not a silent resurrection) | Engine said block (restricted_review, unknown country, individual subscriber, missing LIA/DPA…) |
| `alternative_channel` | Semi-terminal | Email blocked (consent_required); human-executed tasks created (LinkedIn engagement, warm intro, events) |
| `suppressed` | **Terminal** | Global or tenant suppression hit |
| `objected` | **Terminal** | Prospect objected / unsubscribed; suppression written |
| `erased` | **Terminal, absorbing** | Erasure done; only anonymised stub + suppression hash remain |
| `disqualified` | Terminal | Failed qualification, negative reply, or tenant rejected |
| `rescheduled` | Transient | Meeting moved; returns to `booked` |
| `no_show` | Semi-terminal | Prospect didn't attend; may go to `rescheduled` once (configurable) |
| `disputed` | Transient | Tenant disputes attendance/qualification; resolves to `billed` or `attended (non-billable)` |
| `escalated` | Transient | Human must decide; returns to the prior state or any allowed target the human chooses |

Additional internal flag (not a state): `paused_until` (out-of-office, tenant pause, deliverability kill-switch).

## 2. Transition table

| From | To | Trigger | Guards (all must pass) | Effects |
|---|---|---|---|---|
| — | `discovered` | Agent 1 signal accepted | ICP score ≥ tenant threshold; not deduped within window | Link signal |
| `discovered` | `enriched` | Agent 2 success | Validated output; verified email or explicit `email_unverified` flag; field sources recorded | Prospect + field sources stored |
| `discovered` | `disqualified` | Agent 2 found no matching persona | — | — |
| `enriched` | `compliance_checked` | Engine `allow` | See `05-compliance-engine.md` | Decision row |
| `enriched` | `rejected_by_compliance` | Engine `block` | — | Decision row + reason codes |
| `enriched` | `alternative_channel` | Engine `alternative_channel` | — | Human tasks created |
| any non-terminal | `suppressed` | Suppression hit (any check) | — | Stop all scheduled actions |
| `compliance_checked` | `pending_approval` | Agent 3 draft passes self-critique (≥ 8/10) | Every claim has a source; footer present; schema valid | Message drafts stored |
| `compliance_checked` | `escalated` | Agent 3 fails after 2 rewrites / invalid output / refusal | — | Escalation item |
| `pending_approval` | `approved` | Reviewer approves (or edits then approves) | Reviewer has permission; tenant approval mode satisfied (or sampling skipped this lead) | Approval row, edit diff |
| `pending_approval` | `disqualified` | Reviewer rejects | Reason required | Approval row |
| `approved` | `contacted` | Gateway send success (live or dry-run) | **Send-time** compliance + suppression + tenant + deliverability gates | Message `sent`/`dry_run_sent`; follow-ups scheduled |
| `approved` | `rejected_by_compliance` / `alternative_channel` / `suppressed`* | Send-time re-check blocks | — | Message → `blocked_at_send`; compliance log entry |
| `contacted` | `replied` | Inbound reply matched | — | Reply stored, follow-ups paused |
| `contacted` | `disqualified` | Sequence exhausted, no reply | — | Retention clock starts |
| `replied` | `objected` | unsubscribe / objection / DP request | — (always allowed) | **Global suppression same txn**, DP request + deadline if applicable, cancel follow-ups |
| `replied` | `qualified` | Qualification criteria met with evidence | Evidence per criterion; confidence ≥ 0.70 | Qualification row |
| `replied` | `contacted` | out_of_office | Return date parsed (else +7 days default) | `paused_until` = return + 1 business day |
| `replied` | `disqualified` | negative / not a fit | — | — |
| `replied` | `escalated` | anger, legal, pricing, contract, complaint, confidence < 0.70, off-playbook | — | Escalation item |
| `replied` | `contacted` | not_now | Tenant config allows nurture | Re-contact date scheduled (compliance re-checked at send) |
| `replied` | (new lead) `discovered` | referral | — | New prospect, full Agent 2 + compliance re-run; original lead → `disqualified(reason=referred)` |
| `qualified` | `booked` | Booking created via gateway | Slot still free at commit (re-check); within tenant availability & local hours | Meeting row; confirmation + reminders scheduled |
| `qualified` | `qualified` | Slot taken between offer and acceptance | — | Re-offer new slots |
| `booked` | `confirmed` | Prospect accepts invite / replies to confirmation | — | — |
| `booked`/`confirmed` | `rescheduled` | Reschedule link used / provider update | Reschedule count ≤ tenant max (default 3) else `escalated` | Old reminders cancelled |
| `rescheduled` | `booked` | New slot booked | — | New reminders |
| `confirmed`/`booked` | `attended` | Attendance signal (calendar/CRM) or tenant confirmation | Meeting end time passed | Attendance event |
| `confirmed`/`booked` | `no_show` | Tenant marks no-show / no signal + tenant confirms | — | Optional one reschedule offer |
| `attended` | `billed` | Invoice line created | Billing eligibility (see below) | Invoice line with evidence chain |
| `attended`/`billed` | `disputed` | Tenant dispute with reason | Within dispute window (config, default 14 days) | Dispute row |
| `disputed` | `billed` / `attended` | Operator resolves | Reason recorded | Credit note if reversing a billed line |
| `escalated` | (chosen target) | Human decision | Target must be a valid transition from the pre-escalation state | Decision recorded |
| any | `erased` | Erasure request completed | — | Crypto-shred, anonymise, keep suppression hash |

\* If send-time compliance blocks an approved message, the lead moves to `rejected_by_compliance` (or `alternative_channel` / `suppressed`), the message to `blocked_at_send`, and the event is surfaced on the compliance log.

### Special cases
- **Objection after booking** (`booked`/`confirmed` → objection): follow-ups cancelled, global suppression written for *future* outreach, meeting **kept** unless the prospect cancels; the lead keeps its meeting state with flag `objected_after_booking = true`; reminders for that meeting continue only if the tenant confirms (default: reminders suppressed, tenant notified).
- **Erasure after billing**: invoice lines keep anonymised references (meeting ID, date, amount) — whether billing evidence may be retained under a legal-obligation basis is flagged for solicitor review.

## 3. Billing eligibility guard (`attended → billed`)

All of:
1. Meeting `booked_by_system = true`.
2. Qualification evidence satisfies the tenant's **signed** criteria version at booking time.
3. Attendance status `attended` from calendar/CRM signal or tenant confirmation.
4. Not already billed (unique constraint `invoice_lines(meeting_id)`).
5. Tenant pricing plan includes a per-meeting fee.

## 4. Message sub-state machine

`draft → pending_approval → approved → queued → sending → (sent | dry_run_sent | failed_retryable | failed_permanent | needs_reconciliation | blocked_at_send)`; `sent → bounced | complained`.

- `sending` is entered under row lock; crash in `sending` → `needs_reconciliation` (never auto-resend).
- `failed_retryable` (e.g. 429/5xx **before** provider accepted) → backoff retry with the same idempotency key.

## 5. Meeting sub-state machine

`proposed → booked → confirmed → (rescheduled → booked)* → attended | no_show | cancelled`.

## 6. Invariants (property-tested with Hypothesis)

1. No lead reaches `contacted` without an `allow` compliance decision at send time.
2. No suppressed identity ever receives a message in a non-dry-run mode or a dry-run "sent" record after suppression.
3. Terminal states have no outgoing transitions except `→ erased`.
4. At most one `sent`/`dry_run_sent` message per (lead, sequence_step).
5. At most one active meeting per lead.
6. Every transition has exactly one audit row.
