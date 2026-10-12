# Phase 4 — Replies, booking and show rate: Report (2026-10-12)

## 1. Summary
Prospects can now reply, and the system handles every reply safely, end to end, in dry-run:
**reply → deterministic safety rules → Agent 4 (classify, qualify, playbook answer) → slot offer → booking → confirmation, invite and agenda → 24 h and 1 h reminders → pre-meeting brief → attendance.**
It also covers out-of-office (pause and resume), referrals (new lead with a fresh compliance check), unsubscribes, data-protection requests with a one-month legal deadline, human-confirmed erasure, rescheduling and no-shows.

The operator console has three new pages: **Escalations**, **Data requests** (deadline countdown, MFA-protected erasure) and **Meetings** (masked list, logged brief, attendance). AI-written replies reach the existing approval desk with the prospect's message shown beside them.

**413 automated tests pass** (398 backend, 9 component, 6 browser), with zero accessibility violations on the new pages. The Agent 4 eval (75 labelled replies, 16 of them prompt-injection attempts) gives **100% recall on unsubscribe and data-protection requests**, and still ≥95% when the AI model is deliberately wrong every time, because the safety rules don't depend on the model.

As in Phase 3, every AI call ran on the built-in simulator (no API key, per ADR-0011). **Real-model classification and reply quality is not measured yet.**

Testing, review and the Windows run found **10 real defects, all fixed** (§6), including one serious one: "Please delete all the data you hold about me" was not recognised as an erasure request. Nothing left the system: 0 real emails, 0 real calendar events, 0 paid API calls.

Decisions I took under your delegation are recorded in ADR-0011 (R12 accepted, real-model evals approved in principle but on hold, Phase 4 started) and ADR-0012 (Phase 4 design choices, §3).

## 2. Changes made

| Area | Files |
|---|---|
| Migration | `0005_replies_booking.py` (reversible, verified up/down/up): message kinds, idempotent reply ingestion, slot offers, signed reschedule tokens, meeting agenda and responses, reminder links, data-protection request details, new escalation reasons, **redaction-only** trigger on `dry_run_log` / `llm_calls` |
| Tenant config | Qualification criteria (need / authority / timing), objection playbook, calendar and show-rate settings, supported languages, AI disclosure, nurture period |
| Safety rules | `agents/reply_handler/rules.py`: unsubscribe, contact objection, data-protection request type (access, erasure, objection, rectification, restriction, portability) in six languages; out-of-office return dates (UK day-first); referral name and email; anger, legal, complaint, pricing and contract flags; slot choice ("option 2", "the Thursday one", "2nd") |
| Agent 4 | `agents/reply_handler/` + prompts `classify / qualify / respond v1` (locked): model can only add safety classes; verbatim-quote qualification; playbook-only answers; reply gate (no links, prices, guarantees or "I'm human"; UK spelling; ≤ 90 words) |
| Replies workflow | `domain/replies/service.py`: idempotent ingest, unknown senders still unsubscribed, suppression in the same transaction, escalation rules, out-of-office pause, referral with compliance re-run, nurture, booking path |
| Data-protection | `domain/dpr/service.py`: one-calendar-month deadline, UK/EU regime, response templates for a human, erasure across all tables (suppression kept) |
| Booking and show rate | `domain/meetings/service.py`, `domain/outbound/calendar.py` (`CalendarGateway`, dry-run ICS), `adapters/calendar/mock.py`: real availability, re-offer when taken, idempotent booking with compliance recheck, confirmation, reminders, brief, reschedule with limit, attendance |
| Workers | Celery beat `fetch_inbound` and `reply_sla` (5-minute target); outbox `lead.referral_checked` → recovery drafting task |
| API | `/escalations`, `/data-protection-requests` (+ status, MFA-protected `complete-erasure`), `/meetings` (+ logged `brief`, `attendance`), dev-only `/dev/inbound`; public single-use `/r/{token}` reschedule page; approval items now carry `kind` and the prospect's reply |
| Frontend | Escalations inbox, data-request tracker with countdown, meetings with logged brief and attendance buttons; approval desk shows reply drafts with "Their reply" context; new nav and strings |
| Demo | `demo/conversations.py`, CLI `pipelineos demo conversations` |
| Tests / evals | 85 new backend tests, 3 component tests, 1 browser test; Agent 4 eval harness + 75-reply labelled set; e2e script now runs two stages on fresh databases |
| Docs | ADR-0011, ADR-0012, this report, evidence files (`demo-run-phase-4.json`, `evals-phase-4.json`, `traces-phase-4.json`) |

No new dependencies (the reschedule form is parsed without adding `python-multipart`). No paid services.

## 3. Decisions and assumptions (ADR-0011, ADR-0012)
Taken under your delegation, within its limits (legal, ICO, VAT, credentials, spend and go-live stay with you):
- **R12 accepted** (dev-only `braces` lint advisory). Re-checked: still no fix, still dev-only.
- **Real-model evals approved in principle, on hold** until you add an Anthropic API key with a spend cap.
- **Rules first, model second** for anything legal. The model can add "unsubscribe" or "data-protection request", never remove it.
- **Unsubscribes and "stop emailing me" are actioned instantly and recorded as completed requests,** not escalated. Access, erasure and other data-protection requests always go to a person, with a drafted response the agent never sends.
- **Erasure needs a person, an authenticator code and an identity attestation.** The suppression entry is kept so the person is never contacted again.
- **Sales objections are answered only from the tenant's approved playbook.** Anything else goes to a person.
- **AI disclosure is always on.** I changed the default wording to "This reply was drafted with the help of AI." because the earlier "…and checked by our team" would be untrue once sampling mode starts.
- **Confirmations and reminders are fixed templates** and go out without per-message approval; AI-written replies follow the approval mode.
- **Replies in a language the client hasn't approved go to a person.**
- **Objection after booking:** the meeting stays (flagged), reminders stop.

## 4. Requirements traceability (Phase 4 scope)

| Requirement | Status | Evidence |
|---|---|---|
| Inbound replies threaded to the right lead and message, stored encrypted, processed once | Met | `test_duplicate_delivery_is_handled_once`; `ingest()` matches by `In-Reply-To`, then blind index |
| Unsubscribe / data-protection request → immediate global suppression, follow-ups cancelled | Met | `test_unsubscribe_suppresses_globally_and_cancels_follow_ups`, `test_unsubscribe_from_unknown_sender_is_still_honoured` |
| Data-protection request: tracked, one-month deadline, regime, drafted response for approval, tenant notified | Met | `test_data_protection_request_opens_tracked_request_with_deadline`, `test_one_calendar_month_deadline`, `test_regime`, DP tracker UI |
| Erasure | Met (human-confirmed) | `test_erasure_request_requires_human_completion_then_removes_pii`, `test_erasure_requires_mfa_and_identity_attestation` |
| Agent 4 classifies 9 classes; ≥ 95% recall on data-protection request and unsubscribe | Met (pipeline); **real model not measured** | Eval: 75 replies, accuracy 100%, recall 100% on both; ≥ 95% with a hostile model (`test_safety_recall_holds_even_when_the_model_is_wrong`) |
| Escalate on anger, legal, pricing, contract, complaint, confidence < 0.70, off-playbook | Met | `test_angry_or_legal_reply_is_escalated`, `test_unsubscribe_with_a_legal_threat_is_still_escalated`, `classify()` rules |
| Objections answered from the tenant playbook only | Met | Off-playbook → `escalated:off_playbook`; playbook id checked in `draft_reply()` |
| Qualification against configured criteria with evidence | Met | Brief shows per-criterion quotes; `test_qualification_ignores_quotes_the_prospect_never_wrote` |
| AI disclosure per config; never claims to be human | Met | `test_not_now_drafts_reply_for_approval_and_nurtures` (disclosure appended), `test_ai_reply_gate` |
| Prompt injection in replies: no unauthorised action | Met | 14/14 injection attempts escalated, 0 unsafe actions; `test_prompt_injection_in_reply_never_books_or_qualifies`; hidden and multilingual overrides (`test_hidden_and_multilingual_overrides_are_removed`) |
| Different language | Met | `test_unsupported_language_is_escalated_not_answered` |
| Out-of-office: pause, then resume | Met | `test_out_of_office_pauses_follow_ups_until_after_return` (paused on 27 Oct, sent on 4 Nov) |
| Referral → new lead with full compliance re-run | Met | `test_referral_creates_new_lead_through_compliance`, `test_referral_to_a_suppressed_contact_is_blocked` |
| Negative / not now | Met | `test_negative_suppresses_for_tenant_and_disqualifies`, not-now nurture test |
| Booking from real availability (time zones, bank holidays, notice), re-offer if taken, idempotent, via gateway | Met | `test_booking_creates_dry_run_event_confirmation_reminders_and_brief`, `test_taken_slot_is_reoffered_not_double_booked` |
| Calendar disconnected | Met | `test_calendar_disconnected_escalates` |
| Confirmation + invite + agenda; 24 h and 1 h reminders; pre-meeting brief | Met | Booking test; `test_reminders_are_sent_at_offsets`; logged brief access (`test_meetings_are_masked_and_brief_access_is_logged`) |
| Reschedule (link and reply), limit, no-show tracking | Met | `test_reschedule_link_lists_slots_and_moves_the_call_once`, `test_reschedule_request_by_reply_offers_new_slots`, `test_reschedule_limit_escalates`, `test_attendance_and_no_show` |
| Objection after booking | Met | `test_objection_after_booking_keeps_meeting_and_skips_reminders` |
| Escalations inbox and data-protection tracker in the operator UI | Met | Playwright `conversations.spec.ts` with axe; screenshots at 1360 px and 390 px |
| 5-minute classification SLA | Met (monitor) | `reply_sla` beat task, `test_reply_sla_and_referral_recovery_task` |
| Compliance gate unbypassable from every path (incl. bookings and replies) | Met | Bookings recheck compliance; send-gate trigger unchanged; import contracts kept |
| Masked PII by default, logged unmask | Met | Meetings list masked; brief and edit unmask logged |
| Billing, client portal, real inbox/calendar adapters | Deferred | Phase 5 (billing, portal) and Phase 6 (real adapters) |

## 5. Test results

| Suite | Result |
|---|---|
| Backend: `uv run pytest` (unit 246, property 1, contract 13, integration 94, security 39, evals 5) | **398 passed**, 0 failed (≈100 s, real PostgreSQL 16 + pgvector) |
| Frontend components: `pnpm test` | **9 passed** |
| Browser: `scripts/e2e-fullstack.sh`, stage 1 (4 smoke + approval desk) and stage 2 (escalations, data requests, meetings) | **6 passed**, axe: 0 violations |
| Lint / types / import contracts / prompt lock | Clean (ruff, mypy --strict on 122 files, eslint, tsc); 2 contracts kept; 7 prompts locked (new test: every shipped prompt must be locked) |
| Migrations | `0005` up → down to base → up → down to 0004 → up: OK |
| Dependency audits | Backend: none. Frontend production: none. Dev: 1 high (`braces`, accepted as R12) |
| Backend on **your Windows PC** | See §5.1 |
| **Baseline** | Phase 3: 324 → Phase 4: **413**. No previously passing test fails |

**Agent 4 eval** (`docs/evals-phase-4.json`, simulator): 75 replies (13 unsubscribe, 6 contact objections, 13 data-protection requests, 5 out-of-office, 4 referrals, 5 negative, 4 not-now, 7 interested, 2 unclear, 16 injection attempts including hidden characters, French and German overrides, fake system tags and quoted threads). Accuracy 100%; injections 14/14 escalated, 0 unsafe actions; the two "unsubscribe + injection" replies still unsubscribed.
**Not covered:** with the simulator, this mainly measures the rules and the pipeline. A real model's classification and reply quality need the on-hold eval run.

**Demo conversation** (fresh database, `pipelineos demo conversations`, `docs/demo-run-phase-4.json`):

| Company | Prospect's reply | What happened | Final state |
|---|---|---|---|
| Contoso Managed IT | "Timing is good… team stretched out of hours… sort cover before Christmas" | Qualified (need, timing from quotes; authority from title) → 3 slots offered (approved, sent) → "Option 2" → booked → confirmation and invite → accepted → reminders 24 h and 1 h → attended | attended |
| Adventure Works MSP | Out of office until 2 November | Follow-ups paused until 3 November | contacted |
| Woodgrove IT | "Not the right person, speak to Sam Okafor" | New lead for Sam, compliance re-run (allow), drafted and sent; original closed | disqualified + referral contacted |
| Proseware Systems | "Please remove me from your mailing list" | Global suppression, recorded as a completed request | objected |
| Blue Yonder | "Under GDPR, what data do you hold about me?" | Global suppression, access request open, deadline 13 Nov 2026, response drafted for a person, escalated | objected |

Side effects of the whole run, all dry-run: 10 email sends and 1 calendar event in the dry-run log, 32 AI calls all `simulated`, **0 real sends**.

**Required sample-lead traces** (`docs/traces-phase-4.json`): identical decisions to Phase 3 (only database row ids differ).

| Lead | Decision | Outcome |
|---|---|---|
| UK corporate | allow | dry-run sent |
| UK sole trader | alternative channel (individual subscriber) | human-only task |
| German prospect at a UK company | alternative channel (consent required) | human-only task |
| Globally suppressed person | block | suppressed |
| Suppressed after approval | allow at queue, block at send | suppressed, nothing sent |

### 5.1 On your Windows PC
Synced as branch `phase/4-replies-booking` (git bundle, no history rewritten), migrated to `0005`, tested against your Docker PostgreSQL:
- First run: **396 passed, 0 failed** (≈184 s).
- The demo on your existing database exposed **D22**: the database still held the Phase 3 demo configuration (no qualification criteria), and Contoso was asked an empty "qualifying question" in a loop instead of being booked. Fixed (escalate to a person when no criteria exist; re-seeding now stores the current demo configuration as a new version). After the fix: see the final line below.

## 6. Defects found during this phase (all fixed, with regression tests)

| # | Defect | Impact if shipped | Fix |
|---|---|---|---|
| D13 | German "Bitte entfernen Sie mich aus Ihrem Verteiler" wasn't recognised as an unsubscribe | German speakers kept receiving follow-ups until the model caught it | More unsubscribe phrasings in de/nl/es/it |
| D14 | "Please delete all the data you hold about me" wasn't recognised as an erasure request | **A legal request could be missed** if the model also missed it | Broader erasure patterns; regression test and eval cases |
| D15 | "Please don't email me again" also set the *interested* hint ("please do" matched inside "don't") | Safety still won, but the signal was wrong | Pattern fixed |
| D16 | Short French replies weren't detected as French | Unsupported-language escalation could be skipped | Larger stopword list |
| D17 | Instructions hidden with zero-width characters, or written in French or German, passed the injection filter | Hidden instructions would reach the model (structural controls still applied) | Unicode normalisation, invisible characters stripped, multilingual override patterns |
| D18 | Reschedule links used the same signature purpose as unsubscribe links | A token from one link type could be tried on the other | Separate signature purpose; cross-use test |
| D19 | The default AI disclosure said replies were "checked by our team" | Untrue in sampling mode | Wording changed |
| D20 | The unsupported-language rule only applied to *interested* and *not now* replies | Other foreign-language replies were escalated for the wrong reason | Applies to every non-out-of-office reply |
| D21 | The approval desk would have failed to show reply context (wrong encryption context name) | Agent replies unreviewable | Fixed; API test covers it |
| D22 | With no qualification criteria configured, an interested reply produced an empty qualifying question, every time (found on your PC) | Prospects stuck in a loop; no meeting | Escalate to a person; demo re-seed upgrades the stored configuration |

## 7. Risks and open issues

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R18 | Real-model writing **and** reply-handling quality unmeasured | High (business) | Eval suites ready; run when you add a capped API key (ADR-0011 #2) |
| R21 | The six-language safety keyword lists are mine, not reviewed by native speakers or counsel | Medium | Model adds a second layer; ask a native speaker or counsel to review before EU expansion (Stage 3) |
| R22 | Erasure keeps a suppression hash and audit metadata | Medium (legal) | Standard practice to honour objections; **needs your solicitor's confirmation**. Added to the scope of the *UK solicitor review* checklist item (new databases show the longer wording; the app can't reword existing items, by design) |
| R23 | Mock inbox and calendar only; real Microsoft 365 / Google adapters (webhook signatures, OAuth) come in Phase 6 | Medium | Adapters fail loudly in any non-mock mode |
| R24 | Data-protection deadlines don't move for weekends and bank holidays | Low | Conservative (earlier deadline); counsel can confirm |
| R12 | `braces` dev-only advisory | Low | Accepted (ADR-0011); still no fix upstream |
| R15 | LLM prices not configured, cost per lead/meeting is NULL | Medium | Add verified prices to `config/llm_prices.yaml` |
| R4, R5, R16, R17 | Legal and accounting items from Phases 0–2 | High before go-live | Unchanged; on the go-live checklist |

## 8. What you must do
1. **Approve Phase 5**, or ask for changes.
2. **Optional, to unblock R18:** add an Anthropic API key with a monthly spend cap (for example £20) so I can run both eval suites on a real model with synthetic data only.
3. **For your solicitor's list:** the data-protection response templates in `domain/dpr/service.py`, the AI disclosure wording, and R22 (what is kept after erasure).
4. Optionally, try the new demo: `uv run pipelineos demo conversations`, then open Escalations, Data requests and Meetings (`docs/DEMO.md`).

## 9. Next phase plan — Phase 5: billing, client portal and reporting (pending approval)
1. **Billable meetings:** a meeting becomes a billable line only when it was attended, booked by the system and the qualification evidence meets the client's signed criteria. Each line stores the full evidence chain (signal → lead → messages → replies → qualification → meeting → attendance).
2. **Invoices** in GBP (EUR later), with VAT rules as configuration marked `verified_by_accountant: false`; unknown → block and flag. Dry-run only: no payment provider until go-live.
3. **Disputes:** the client can dispute a line within a set window with a reason; the operator resolves it; everything logged.
4. **Client portal:** meetings and briefs, pipeline, data-protection requests that concern them, invoices and disputes; masked by default, tenant-isolated, read-mostly.
5. **Reporting:** reply rate, positive-reply rate, meetings booked, show rate, cost per lead and per meeting (once prices are set), compliance blocks by reason.
6. **Client onboarding:** tenant configuration, footer details, privacy notice and qualification criteria with a sign-off step.
7. **End-to-end test:** approve → dry-run send → reply → book → confirm → attend → bill.
8. Phase 5 report with traces.
