# Phase 5 — Billing, client portal and reporting: Report (2026-10-12)

## 1. Summary
The loop now closes, in dry-run: **approve → send → reply → book → confirm → attend → bill**.

A meeting becomes a billable line only when every link of its evidence chain holds: the system booked it, attendance came from the calendar or the client, the prospect was qualified against criteria the client had accepted beforehand, and the client accepted the pricing. Monthly draft invoices carry the set-up fee, the retainer, one line per billable meeting (with its evidence chain), and credits for disputes upheld after an invoice was issued.

**Issuing is deliberately blocked.** An invoice can only be issued once the go-live checklist is signed off, your accountant's review of the tax rule is recorded (by you, with your authenticator code), and your company details replace the placeholders in `config/supplier.yaml`. Once issued, the database makes the invoice impossible to edit or delete.

**New screens:**
- **Client portal:** results, meetings with a billable or not-billable status and a reason, dispute form, invoices, and terms to accept.
- **Operator console:** Billing (drafts, issue, disputes, VAT-threshold monitor), Clients (onboarding checklist and activation) and Reports.

**464 automated tests pass:**

| Kind | Count |
|---|---|
| Backend | 447 |
| Component | 12 |
| Browser | 5 smoke and approval, plus 3 conversations, billing and portal |

There are zero accessibility violations on the new pages.

Nothing left the system: 0 real emails, 0 calendar events, 0 invoices issued, 0 paid API calls.

## 2. Changes made

| Area | Files |
|---|---|
| Migration | `0006_billing_portal.py` (reversible; the downgrade refuses rather than deletes if issued invoices or sign-offs exist). Adds:<ul><li>client sign-offs (append-only)</li><li>supplier profile (versioned)</li><li>gapless invoice counters</li><li>invoice period, blocked reasons and issue/void fields</li><li>voidable lines and credit lines</li><li>dispute kind and window</li><li>**immutability triggers** for issued invoices and their lines</li><li>**restrictive RLS**: only the operator or the system writes invoices; clients raise disputes; who may sign terms, and as whom</li></ul> |
| Config | `Pricing` in tenant config (set-up, retainer, per meeting, payment terms, dispute window); `config/supplier.yaml` (your details, placeholders until you fill them) synced to `supplier_profiles` |
| Billing | `domain/billing/eligibility.py` (pure rules with reason codes), `tax.py` (rule selection, half-up rounding), `service.py`: <ul><li>drafts with evidence chain</li><li>issue guard</li><li>gapless numbering</li><li>disputes</li><li>credits</li><li>accountant sign-off</li><li>VAT threshold</li></ul> |
| Terms and onboarding | `domain/tenants/signoffs.py` (snapshot + hash), `domain/tenants/onboarding.py` (create, LIA, DPA, checklist, activate) |
| Reporting | `domain/reporting/metrics.py`: <ul><li>funnel</li><li>show rate</li><li>billed meetings and revenue</li><li>AI cost (only when every call is priced)</li><li>compliance blocks</li><li>escalations</li></ul> |
| API | `/invoices` (list, detail, `draft`, MFA `issue` and `void`), `/billing/meetings` (eligibility), `/disputes` (client raises, operator resolves), `/billing/vat-threshold`, `/tax-rules` (+ MFA accountant verification), `/clients` (create, config, LIA, DPA, users, record terms, activate), `/terms` (+ client `sign`), `/reports/metrics`. New permissions: `sign_terms`, `view_billing`, `manage_billing` |
| Frontend | Client portal (shell, overview, meetings with dispute, invoices, terms); operator Billing, Clients, Reports; invoice shown as a paper document with each meeting's evidence chain; print styles |
| Demo | Demo client admin who accepts criteria and fictional pricing (£250 set-up, £500/month, £300 per meeting); CLI `pipelineos demo billing` |
| Tests | 49 new backend tests: unit, property, integration and API security. Also 3 component tests and 2 browser tests; the end-to-end script now runs the billing stage |
| Docs | ADR-0013, this report, `demo-run-phase-5.json`, `traces-phase-5.json` |

No new dependencies. No paid services.

## 3. Decisions (ADR-0013)
- **Billable means the whole evidence chain holds.** Attendance recorded only by our own operators doesn't count: it has to come from the calendar or the client.
- **Clients accept criteria and pricing themselves in the portal.** What they accepted is stored as a snapshot with a fingerprint, so changing either requires a new acceptance. An offline acceptance can be recorded only with an evidence reference.
- **Invoices are monthly in arrears.** Drafts can be rebuilt freely. A month with an issued invoice is closed, and later corrections become credits on the next invoice.
- **Gapless invoice numbers** (`INV-2026-0001` and so on) are assigned only at issue, so abandoned drafts never burn a number.
- **Tax: unknown → block.** The most specific matching rule wins. A missing or ambiguous rule blocks the invoice, as does a rule your accountant hasn't reviewed.
- **No payment provider and no invoice emails yet.** Both are real side effects that need your choices.

## 4. Requirements traceability (Phase 5 scope)

| Requirement | Status | Evidence |
|---|---|---|
| Billing eligibility guard (booked by system, attended via calendar/client, qualified against criteria signed at booking, not billed, per-meeting fee) | Met | `test_billing_rules.py`: each rule individually, plus a property test (billable ⇔ all rules hold) |
| Every billed line stores the evidence chain (signal → lead → messages → replies → qualification → meeting → attendance) | Met | `test_draft_bills_setup_retainer_and_the_attended_meeting_with_evidence`; ids only, survives erasure |
| Invoices in GBP/EUR with VAT rules as config, `verified_by_accountant: false`, unknown → block | Met | `test_tax_rule_selection` (no rule / ambiguous → block), issue blocked until accountant sign-off |
| Sequential invoice numbers; issued invoices unalterable | Met | `test_issue_assigns_gapless_numbers_bills_the_lead_and_freezes_the_invoice` (5 tampering attempts refused by the database) |
| Disputes within a window, resolution recorded, credit note when reversing a billed line | Met | `test_upheld_dispute_before_issue_removes_the_line`, `…after_issue_credits_the_next_invoice`, `test_rejected_dispute…`, `test_dispute_window_and_duplicates` |
| Pricing combinations (set-up, retainer, per meeting) | Met | Draft tests; next-month invoice bills the retainer only |
| Client portal: meetings and briefs, pipeline, invoices, disputes; masked by default; tenant-isolated | Met | `test_billing_api.py`; Playwright `billing.spec.ts`; isolation tests (another tenant sees 0 invoices and 0 sign-offs) |
| Reporting: reply rate, positive replies, meetings, show rate, cost per meeting, compliance blocks | Met (cost needs prices, R15) | `test_metrics_for_the_demo_month`, `test_reports_are_scoped_to_the_callers_tenant` |
| Client onboarding with sign-off | Met | `test_onboarding_checklist_gates_activation`, `test_onboarding_endpoints` |
| VAT threshold monitoring | Met | `test_vat_threshold_monitor_warns_before_the_limit` |
| MFA for destructive / financial actions | Met | Issue, void and accountant sign-off refuse a wrong code (`test_issuing_requires_mfa…`, `test_accountant_signoff_needs_mfa…`) |
| E2E: approve → send → reply → book → confirm → attend → bill | Met (dry-run; bill = draft invoice line) | Two-stage `scripts/e2e-fullstack.sh` |
| Payment collection, invoice delivery, real adapters, deployment | Deferred | Phase 6 |

## 5. Test results

| Suite | Result |
|---|---|
| Backend `uv run pytest` | **447 passed**, 0 failed (≈235 s, real PostgreSQL 16 + pgvector). Breakdown: <ul><li>unit 272</li><li>property 1</li><li>contract 13</li><li>integration 109</li><li>security 47</li><li>evals 5</li></ul> |
| Frontend components | **12 passed** |
| Browser (2 stages, fresh databases) | **8 passed**, axe: 0 violations on billing, reports, clients and all four client-portal pages |
| Lint / types / import contracts / prompt lock | Clean (ruff, mypy --strict on 132 files, eslint, tsc); 2 contracts kept; 7 prompts locked |
| Migrations | 0006 up → base → up → 0005 → up: OK |
| Dependency audits | Backend: none. Frontend production: none (dev-only `braces`, R12, unchanged) |
| Agent 4 eval | Unchanged from Phase 4 (75 replies, 100% safety recall) |
| Backend on **your Windows PC** | **447 passed, 0 failed** (§5.1) |
| **Baseline** | Phase 4: 413 → Phase 5: **464**. No previously passing test fails |

**Demo** (fresh database, `demo seed` → `run` → `conversations` → `billing`, `docs/demo-run-phase-5.json`):

| Line | Amount |
|---|---|
| Set-up fee | £250.00 |
| Monthly service, October 2026 | £500.00 |
| Qualified meeting attended: Contoso Managed IT Ltd, 15 Oct 2026 (6-step evidence chain) | £300.00 |
| **Total** (not VAT-registered: 0%) | **£1,050.00** |

The draft can't be issued yet. It shows exactly why:
- the go-live checklist isn't signed off
- the company details are still placeholders
- the tax rule hasn't had an accountant's review

**Required sample-lead traces** (`docs/traces-phase-5.json`): identical decisions to Phases 3–4.
- UK corporate → dry-run sent.
- Sole trader → alternative channel.
- German prospect → alternative channel.
- Suppressed → blocked.
- Suppressed after approval → blocked at send.

**No external side effects:**
- the dry-run log holds 11 email sends and 1 calendar event
- 0 messages were actually sent
- 1 invoice exists, as a draft
- the invoice counter was never used
- every AI call was `simulated`

### 5.1 On your Windows PC
Synced as branch `phase/5-billing-portal` (git bundle, no history rewritten). Your development database was migrated to `0006` (nothing deleted).
- Backend against your Docker PostgreSQL: **447 passed, 0 failed** (≈306 s).
- Demo on a fresh database on your PC (`pipelineos_demo5`): the same October draft. Set-up £250, retainer £500, Contoso meeting £300, total £1,050.00, with the same three reasons it can't be issued.

## 6. Defects found during this phase (all fixed, with tests)

| # | Defect | Impact if shipped | Fix |
|---|---|---|---|
| D23 | Since Phase 2, a client account could, at database level, update its own invoice rows. The API never allowed it; only the row-level policy did | A bug in a future endpoint could have let a client change an invoice | Restrictive policies: only the operator or the system writes invoices; test proves the client's update touches 0 rows |
| D24 | The original "one line per meeting" constraint would have stopped a meeting being re-billed after its draft was rebuilt | Rebuilding a draft would have failed or dropped meetings | Uniqueness now applies to active lines only; voided drafts are kept for audit |
| D25 | Invoice lines created in the same transaction had no stable order, and the "DRAFT" stamp covered the invoice number | Confusing documents | Fixed ordering; stamp moved |

## 7. Risks and open issues

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R25 | Demo pricing is fictional; real pricing, payment terms and the dispute window are commercial decisions | High (business) | Yours to set per client; clients must accept them in the portal |
| R26 | VAT treatment (registration status, EU reverse-charge wording, threshold figure) is unverified config | High before invoicing | Accountant review, then you record it (MFA); invoices can't be issued before |
| R27 | Keeping billing evidence after a prospect's erasure (ids only) needs a legal basis | Medium | On the solicitor list since Phase 4 (R22) |
| R28 | No payment collection or invoice delivery | Medium | Phase 6, after you choose a provider |
| R18, R15, R12 | Real-model quality unmeasured; AI prices not set; dev-only advisory | As before | Unchanged |

## 8. What you must do
1. **Approve Phase 6** (production readiness), or ask for changes.
2. **Decide your real pricing model.** The demo's £250 + £500/month + £300 per meeting is only an example.
3. **Before any invoice can be issued (not needed now):**
   - fill in `config/supplier.yaml`
   - have your accountant confirm the VAT settings and rules
   - record that review in the dashboard
4. Optionally, try it: `pipelineos demo conversations` then `pipelineos demo billing`; sign in as the operator (Billing, Reports, Clients) and as the demo client (the seed prints its one-time password).

## 9. Next phase plan — Phase 6: production readiness (pending approval)
1. **Real adapters behind the three-key switch:**
   - Microsoft 365 / Google mail and calendar (OAuth, webhook signatures)
   - Companies House
   - a licensed enrichment provider
   - an email verifier

   All stay in sandbox or dry-run until go-live.
2. **Hosting in the UK:**
   - cost comparison and infrastructure as code
   - secrets manager
   - TLS
   - backups with a tested restore
   - separate staging
3. **Observability:**
   - metrics and alerts (kill switch, reply SLA, data-protection deadlines, VAT threshold, error rates)
   - log retention
4. **Security:**
   - secret scanning and container scanning in CI
   - dependency update bot
   - pen-test checklist
   - session and rate-limit review
   - load test (10k leads/day; dashboard < 2 s at 100k leads)
5. **Delivery:**
   - client invite emails
   - invoice delivery and a payment link (after you choose a provider)
6. **Real-model eval run** (if you add a capped key) and LLM prices for cost per meeting.
7. **Go-live runbook** and the final checklist for you to sign (Phase 7 is the go-live itself, yours to approve).
