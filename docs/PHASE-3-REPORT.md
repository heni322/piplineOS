# Phase 3 — MVP Vertical Slice (UK): Report (2026-10-12)

## 1. Summary
The full UK slice now runs end to end for one fictional client, one niche (UK IT managed service providers), in English, with mocked providers:
**Agent 1** (signals) → **Agent 2** (decision-maker) → **compliance gate** → **Agent 3** (personalised, source-cited drafts with self-critique) → **human approval desk** → **dry-run send** through the Phase 2 gateway, with follow-ups scheduled and cancelled safely.

A realistic synthetic demo (14 signals, 12 companies) reaches exactly the expected outcome for every company. The operator UI's approval desk shows each email as a letter, with every factual claim highlighted and linked to its source. **324 automated tests pass**, including a full-stack browser test (login with an authenticator code, approve, edit, reject) with zero accessibility violations.

No API key was provided, so every AI call ran on a built-in **simulator** (recorded as `simulated`). **The quality of a real model's writing has therefore not been measured yet.** The pipeline guarantees (grounding, footer, length, spelling, safety gate) have been measured.

Testing found **4 real defects, all fixed** (§6). Nothing left the system: 0 real emails, 0 paid API calls.

## 2. Changes made

| Area | Files |
|---|---|
| Migration | `0004_phase3_messages.py`: subject options, writer source snapshot, edited subject on approvals, `sampled` auto-approvals (reversible; the downgrade refuses rather than deletes if sampled approvals exist) |
| Tenant config | `domain/tenants/config.py`: versioned ICP, personas, brand voice, sequence, thresholds |
| Agent 1 | `agents/trigger_hunter/` + `prompts/trigger_hunter/score.v1.md`: rate-limited sources, per-company dedupe window, ICP score 0–100 with reasoning, country tagging |
| Agent 2 | `agents/decision_maker/`: licensed-enrichment mock, email verification, Companies House legal form → subscriber type, per-field sources, whole-word persona matching, duplicate-prospect guard |
| Agent 3 | `agents/writer/` + `prompts/writer/{draft,critique}.v1.md`: opener ≤ 90 words with one cited detail, follow-ups, deterministic footer, deterministic gate, self-critique, max 2 rewrites, then a human |
| Injection defence | `agents/injection.py` (sentence-level sanitiser), escaped untrusted blocks, output gate |
| LLM | `llm/simulator.py`, `llm/factory.py` (three-key rule for paid calls) |
| Approvals | `domain/approvals/service.py`: approve / edit (diff logged, masked-token and link guards) / reject (reason required); sampling mode with deterministic, auditable sampling |
| Orchestration | `workflows/pipeline.py`, Celery `process_lead` + beat `send_due`, outbox `lead.discovered` → `process_lead` |
| Gateway | Follow-ups are cancelled unless the lead is still `contacted` |
| API | `/approvals` (masked), `/approvals/{id}` (logged unmask), `/approvals/{id}/decision` (Idempotency-Key required), `/pipeline/summary`, `/compliance/log`, `/dry-run`, `/tenants`, demo-scale `/pipeline/run`, `/send/run` |
| Demo | `demo/dataset.py`, `demo/seed.py`, CLI `demo seed / run / send`, `docs/DEMO.md` |
| Frontend | Operator shell with sign-in guard, overview ledger, **approval desk** (cited letter, keyboard shortcuts A/E/R, logged unmask-to-edit, reject with reason), compliance log, dry-run log |
| Tests / evals | 75 new backend tests, 2 component tests, 1 full-stack Playwright test, Agent 3 eval harness + labelled guard dataset |
| Docs | ADR-0010, `PHASE-3-REPORT.md`, `DEMO.md`, evidence JSON files |

No new dependencies. No paid services.

## 3. Decisions and assumptions (ADR-0010)
- **Paid AI calls follow the same three-switch rule as sending.** Without all three switches on, the simulator answers. This is the strict reading of your "no paid API calls unless live and go-live approved". Running quality evals on a real model therefore needs **your explicit exception** (see §8).
- **Agent 2 uses rules, not an LLM.** Cheaper and more predictable.
- **One approval covers the whole sequence** (opener plus two follow-ups), to keep review under 30 seconds.
- **Names are masked in the queue, even inside the email text.** Editing requires a logged reason to reveal them.
- **Follow-ups go out 3 and 6 recipient-local business days** after the opener, and only while there has been no reply, objection or suppression.
- **Demo-scale endpoints run the pipeline and sending synchronously** and are refused in staging and production. Production uses the Celery workers.

## 4. Requirements traceability (Phase 3 scope)

| Requirement | Status | Evidence |
|---|---|---|
| Realistic demo with mock data, one tenant, one niche, English | Met | `demo/dataset.py`; `test_every_demo_company_reaches_its_expected_state`; `docs/demo-run-phase-3.json` |
| Agent 1: pluggable rate-limited sources, output schema, ICP score + reasoning, country tag, per-company dedupe window | Met | `test_signal_fields_and_dedupe` (14 fetched, 1 duplicate, 1 below threshold, 12 discovered) |
| Agent 2: licensed enrichment only, verified email, per-field sources, subscriber type, output schema | Met | `test_pipeline` outcomes (risky email blocked, sole trader → individual, unknown legal form blocked); `test_role_relevance` |
| Compliance gate before Agent 3 (no tokens spent on blocked leads) | Met | No messages for blocked leads (`test_alternative_tasks_for_blocked_email`); 23 AI calls for 13 scores + 5 drafts + 5 critiques |
| Agent 3: native en-GB, ≤ 90-word opener, one verifiable cited detail, low-friction CTA, footer, every claim stored with source URL, unsourced claims never sent | Met (pipeline); **real-model copy quality not measured** | `test_drafts_are_grounded_and_compliant`; eval Part A: 40/40 bundles, 0 unsourced claims, 0 missing footers, 0 over 90 words, 0 US spellings |
| Self-critique rubric; rewrite if < 8; max 2 rewrites; then human | Met | `test_writer_escalates_after_two_rewrites` (exactly 3 drafts per lead, then an escalation) |
| Invalid LLM output → retry with feedback → escalate | Met | `test_invalid_llm_json_escalates_as_llm_failure`, Phase 2 contract tests |
| Prompt injection in scraped content not obeyed | Met | `test_prompt_injection_in_source_is_not_obeyed`, `test_injection.py`, guard eval case "injection obeyed" |
| Approval queue: email, each claim with a clickable source, compliance decision, quality score; approve / edit / reject; every decision and edit logged | Met | `test_operations_api.py`, `test_edit_is_logged_with_diff_and_guarded`, full-stack Playwright test |
| Approvable in under 30 s | Met (design); not timed with real users | One screen, verdict first, single-key shortcuts; `review_seconds` is recorded for every decision |
| Approval mode on by default; sampling afterwards | Met | DB-enforced 2-week lock (Phase 2); `test_sampling_mode_auto_approves_deterministically` |
| Dry-run send via the gateway, business hours, follow-ups | Met | `test_approve_then_dry_run_send_and_schedule_follow_ups`, `test_follow_up_never_sent_after_reply` |
| Idempotency on endpoints with side effects | Met | Decision endpoint replay / conflict tests; pipeline re-run creates no duplicates |
| Masked PII, logged unmask (operators in Tunisia) | Met | `test_queue_shows_everything_needed_and_masks_names`, `test_unmask_for_editing_is_logged` |
| WCAG 2.1 AA, responsive, empty / loading / error states | Met (automated checks) | axe: 0 violations on the approval desk; screenshots checked at 1360 px and 390 px, light and dark |
| LLM tokens and cost per tenant and lead | Partially met | Logged per call; cost is NULL until verified prices are set in `config/llm_prices.yaml` |
| Agent 4, booking, show rate, billing | Deferred | Phases 4–5 by plan |

## 5. Test results

| Suite | Result |
|---|---|
| Backend: `uv run pytest` (unit 202, property 1, contract 12, integration 67, security 29, evals 2) | **313 passed**, 0 failed (≈61 s, real PostgreSQL 16 + pgvector) |
| Frontend components: `pnpm test` | **6 passed** |
| Browser: `scripts/e2e-fullstack.sh` (4 smoke + 1 full-stack review flow with axe) | **5 passed** |
| Lint / types / import contracts / prompt lock | Clean (ruff, mypy --strict on 104 files, eslint, tsc); 2 contracts kept; 4 prompts locked |
| Dependency audits | Backend: none. Frontend: 0 critical, 1 high (the same `braces` item, still no fix exists) |
| Backend on **your Windows PC** (Docker Postgres + Valkey) | **313 passed, 0 failed** |
| **Baseline** | Phase 2: 246 → Phase 3: **324**. No previously passing test fails |

**Agent 3 eval** (`docs/evals-phase-3.json`):
- **Part A (40 bundles, simulator):** 0 unsourced claims, 0 missing footers, 0 over 90 words, 0 wrong-language, 0 US spellings, 0 gate failures.
- **Part B (guard):** recall 30/30 on flawed drafts, 0/10 false positives on clean ones.
- **Not covered:** this measures the safety gate and pipeline, not how persuasive a real model's copy is.

**Demo outcomes** (fresh database, `pipelineos demo run`):

| Company | Outcome | Why |
|---|---|---|
| Contoso Managed IT, Woodgrove IT, Adventure Works MSP, Proseware Systems, Blue Yonder | pending approval | UK limited companies, verified email, relevant role (England, Wales, Scotland) |
| Fabrikam Cloud | alternative channel | IT Director located in Germany: the stricter German rule applies |
| (sole trader, name encrypted) | alternative channel | Individual subscriber under PECR |
| Tailspin Networks (Belfast) | suppressed | Objected via another campaign (global suppression) |
| Litware Solutions (Dublin) | rejected by compliance | Republic of Ireland not enabled (restricted review) |
| Coho Data Services | rejected by compliance | No registry match, so subscriber type unknown |
| Wide World IT | rejected by compliance | Email verification "risky" |
| Graphic Design Institute | disqualified | No contact matching the buyer persona |
| Northwind Bakery / Contoso 2nd posting | no lead | Below the ICP threshold / duplicate within the window |

**On your PC**, the same demo gave Woodgrove as *suppressed* rather than *pending approval*. That is correct: the Phase 2 traces run earlier on that database had recorded the same fictional person unsubscribing under another demo client, and global suppression applies across all clients.

**Required sample-lead traces** (`docs/traces-phase-3.json`): unchanged and passing.
- UK corporate → dry-run sent.
- UK sole trader → alternative channel.
- German prospect → alternative channel.
- Suppressed person → suppressed.
- Suppressed after approval → blocked at send.

No real side effects: 0 messages in a live or sandbox mode, and all 23 AI calls `simulated`.

## 6. Defects found during this phase (all fixed, with regression tests)

| # | Defect | Impact if shipped | Fix |
|---|---|---|---|
| D9 | A cited claim the source never stated still passed the writer gate | Invented "facts" could reach approval looking sourced | Grounding check: every figure and most content words must appear in the cited source (found by the eval) |
| D10 | The persona title "cto" matched inside "Dire**cto**r" | Every Director looked like a perfect buyer persona | Whole-word matching |
| D11 | The figure check treated the full stop after a number as part of it | Good drafts falsely escalated | Number pattern fixed |
| D12 | Signal summaries were built from job headlines | Unnatural openers ("I saw that Hiring: …") | Description first, headline last |

## 7. Risks and open issues

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R18 | **Real-model writing quality is unmeasured.** The simulator proves the pipeline, not persuasiveness | High (business) | Run the eval suite on Claude once you approve an exception (§8); the gate stays in place either way |
| R19 | Lexical grounding can be fooled by paraphrases that reuse source words with a different meaning | Medium | Human approval is mandatory for the first 2 weeks; add a model-based entailment check in Phase 6 |
| R12 | `braces` dev-only advisory, no upstream fix | Low (formally high) | **Still awaiting your acceptance** |
| R15 | LLM prices are not configured, so cost per lead is NULL | Medium (primary business metric) | Add verified prices to `config/llm_prices.yaml` |
| R20 | The 30-second review target is untimed with real users | Low | `review_seconds` is captured; check after the first live week |
| R4, R5, R16, R17 | Legal and accounting items from Phases 0–2 | High before go-live | Unchanged; on the go-live checklist |

## 8. What you must do
1. **Approve Phase 4**, or ask for changes to the slice.
2. **Decide on real-model evals.** Either (a) allow a capped, synthetic-data-only exception to the three-switch rule for running the Agent 3 eval suite against Claude, which needs an Anthropic API key with a monthly cap, or (b) keep the simulator until go-live. I recommend (a): it's the only way to measure R18.
3. **Accept or reject R12** (still open).
4. Optionally, try the demo: `docs/DEMO.md`.

## 9. Next phase plan — Phase 4: replies, booking and show rate (pending approval)
1. **Inbound replies:** mock inbox adapter, threading to leads and messages, raw text encrypted, 5-minute classification SLA monitor.
2. **Deterministic safety rules first:** unsubscribe and data-protection requests (EN/FR keywords) → immediate global suppression, a data-protection request record with its one-month deadline and regime, and a drafted response for approval. The LLM can only *add* to these, never remove them.
3. **Agent 4:** classify (9 classes), handle objections from the tenant's playbook only, qualify against BANT/MEDDIC/custom criteria with per-criterion evidence, disclose AI involvement per config, and escalate on anger, legal/pricing/contract questions, complaints, confidence < 0.70 or anything off-playbook.
4. **Edge cases:** reply in another language; out-of-office with a return date (pause and resume); referral → new lead with a fresh compliance check; objection after booking.
5. **Booking:** mock calendar availability with time zones and bank holidays, re-offer if a slot is taken, idempotent booking through the gateway.
6. **Show rate:** confirmation with invite and agenda, 24 h / 1 h reminders, pre-meeting brief, reschedule link, no-show and reschedule tracking.
7. **Escalations inbox and data-protection tracker** in the operator UI.
8. **Agent 4 eval:** ≥ 50 labelled English replies including data-protection requests and prompt-injection attempts; ≥ 95% recall on `data_protection_request` and `unsubscribe`.
9. Phase 4 report with traces.
