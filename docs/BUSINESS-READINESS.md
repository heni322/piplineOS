# PipelineOS: business readiness (2026-10-15)

> Companion documents: [GO-TO-MARKET.md](GO-TO-MARKET.md) (model, pricing, ICP, pilot plan, unit economics, risks)
> and [LEGAL-REVIEW-CHECKLIST.md](LEGAL-REVIEW-CHECKLIST.md) (what the solicitor must review).

## 1. Verdict

**Business readiness: 5.1 / 10.** It was about 4.0 before this phase.

PipelineOS is a technically strong system:
- 806 backend tests, 19 browser tests with WCAG checks, strict typing;
- database-enforced tenant isolation and a three-key send switch.

It is **not yet a business**:
- No email has been sent to a real person.
- No solicitor has reviewed the legal position.
- No mailbox has been warmed.
- No customer has paid.

None of those four can be done by writing code. **This score cannot honestly pass about 6/10 until a solicitor
and a first pilot client exist.**

What 9/10 needs, and what is missing today:

| Required for 9/10 | Status |
|---|---|
| A real pilot with a paying (or committed) client | **MISSING**: no client yet |
| Solicitor's review of UK outreach, LIA, notice, DPA, terms | **MISSING**: `LEGAL REVIEW REQUIRED` throughout |
| Deliverability validated on real domains and mailboxes | **MISSING**: no domain bought, no warm-up run |
| Payment validated (an invoice issued and paid) | **MISSING**: invoices are drafts, there is no payment provider, and tax rules are not checked by an accountant |
| Backups restored and tested in production | **PARTIAL**: restore drill run for real on 2026-10-15 (encrypted backup → new database → 63 tables identical; a tampered backup refused and cleaned up). Not on a production server yet |
| External security test | **MISSING**: internal tests, scans and threat model only |

## 2. Audit

Scores are 0–10 and evidence-based. "Built" means implemented and tested on synthetic data in dry-run.

| Area | Current state | Target (9/10) | Gap | Impact | Priority | Score |
|---|---|---|---|---|---|---|
| Product (core pipeline) | 5 agents, compliance gate, approvals, replies, booking; all dry-run | Proven on real prospects | No real run | High | P0 (external) | 7 |
| Onboarding | **Guided next step per client**; offer, criteria and pricing form from a niche template; sending domain, inbox and campaign from the console; client accepts terms in the portal | Client self-onboards in under 1 hour | Operator-driven, not self-serve | Medium | P2 | 7 |
| Lead acquisition | Free sources: UK/EU contract awards, Companies House filings and officers, job boards, websites, insolvency filter | Measured leads/week per ICP | **Not tested live from this environment** (network policy); free registers rarely give an email | **High** | P0 | 4 |
| Data quality | Register-verified companies; website proven by company number; data minimisation | Measured accuracy and bounce rate | No measurement | High | P1 | 5 |
| Outreach | Writer with sourced claims, critique, rewrites, quality score 0–10, human approval of every email | Reply rate measured and improved | Evals are synthetic | High | P1 | 6 |
| Deliverability | DNS check (SPF/DKIM/DMARC), warm-up, inbox ≤50/day, campaign caps, automatic kill switch (bounce 3%, complaint 0.3%), business hours, one-click unsubscribe | Seed-tested placement on warmed domains | No domain, no mailbox, no placement test | **High** | P0 (external) | 4 |
| Compliance: technically implemented | Engine at queue and send time, global suppression, per-country rules, DPR handling, audit log, AI disclosure | n/a | n/a | n/a | n/a | 8 |
| Compliance: legally validated | Nothing verified by counsel; GB rules flagged unverified | Solicitor's sign-off recorded | **Everything** | **Blocking** | P0 (external) | 0 |
| Security | RLS forced, MFA, CSRF, encrypted PII, blind indexes, append-only audit, gitleaks/trivy in CI, threat model | External penetration test | No external test | High | P1 (external) | 7 |
| Sales workflow (replies) | Classification with confidence; safety classes always go to a human; objection playbook | Accuracy measured on real replies | Local-model accuracy measured on synthetic replies only (98.4% English) | Medium | P1 | 6 |
| Meeting booking | Calendar adapters (M365/Google), slots, reminders, attendance events | Real bookings | Dry-run only | Medium | P1 | 5 |
| Billing | Billable meeting = system-booked + attended (confirmed by calendar/CRM/client) + qualified against SIGNED criteria + signed pricing + no dispute; evidence chain; dispute window; invoices; **payments recorded (append-only, reversals), paid status, overdue alert** | An invoice issued and paid by a real client | No real invoice paid; no payment provider; tax rules not checked by an accountant | High | P1 (external) | 6 |
| Analytics | **New:** delivered, bounces, complaints, KPIs per 100 people reached, qualified meetings, dry-run flag, client ROI estimate | Measured against a pilot | No real data | Medium | done/P1 | 7 |
| Operations | Backups and restore scripts, Prometheus metrics, alerts, runbooks, **new emergency stop that holds work** | Staging and production, restore drill done | No staging or production server | High | P1 | 6 |
| Customer success | Portal: results, meetings with evidence, disputes, CRM, emergency stop, ROI, **testimonials with consent scope** | Reviews and renewal with real clients | No real client; renewal is manual | Medium | P2 | 5 |
| Scalability | Celery workers, per-tenant caps, load test (Phase 6) | Proven at 10+ clients | Single server design | Low now | P2 | 5 |

**Overall: 5.1 / 10.** This is the weighted view: the four "MISSING" rows of section 1 dominate.

## 3. Top 20 blockers

| # | Blocker | Priority | Who | Status |
|---|---|---|---|---|
| 1 | Solicitor review of the UK legal position (PECR reg. 22 corporate subscribers, UK GDPR legitimate interests, Art. 14 notice) | P0 | **Solicitor** | open |
| 2 | Client contract: terms of service, DPA, billable-meeting definition, dispute process | P0 | **Solicitor + owner** | drafts in code only |
| 3 | First pilot client found and signed | P0 | **Owner** | open |
| 4 | Sending domain(s) bought, DNS set, 2 mailboxes, 21-day warm-up | P0 | **Owner (domain/mailbox)** | open |
| 5 | Production server (VPS, TLS, secrets, backups) and a staging copy | P0 | **Owner** (+ me for config) | runbooks exist |
| 6 | Supplier details (`config/supplier.yaml`), ICO registration fee | P0 | **Owner** | placeholder |
| 7 | Emergency stop that holds work, not destroys it | P0 | me | **DONE (this phase)** |
| 8 | Pilot Mode: hard cap on new people per campaign | P0 | me | **DONE (this phase)** |
| 9 | KPIs per 100 people reached; demo numbers labelled as demo | P0 | me | **DONE (this phase)** |
| 10 | Contactable rate: free registers rarely give an email | P0 (business) | **Owner decision** | see GO-TO-MARKET §7 |
| 11 | Live check of every free source | P1 | **Owner's PC** (blocked here) | command exists |
| 12 | Payment collection and accountant check of tax rules | P1 | me (recording) + **accountant, payment provider** | **recording built (6.9)**; accountant check and provider still open |
| 13 | Unit-economics model to set prices and pilot size | P1 | me | **DONE (this phase)** |
| 14 | Public landing page | P1 | me | **DONE (this phase)**; needs a domain and contact email |
| 15 | Onboarding a client in the console (it needed JSON and SQL) | P1 | me | **DONE (6.8)**: guided by the operator; a client self-serve wizard is still open |
| 16 | External penetration test | P1 | **Pen tester** | open |
| 17 | Inbox-placement (seed) test before each campaign | P1 | me + owner (seed mailboxes) | open |
| 18 | Customer proof capture (consent to quote results) | P2 | me | **DONE (6.10)** |
| 19 | PipelineOS selling itself (operator's own tenant) | P2 | me, then owner + solicitor | **built (6.14)**; needs our own LIA, notice and domain |
| 20 | Automatic data-retention schedule | P1 | me, then solicitor sets the periods | **built (6.7); off until the periods are set** |

The explainable lead score is not on the list: it is mostly built. The approval desk already shows the signal, its
source link, the relevance score, every claim with its source, and the quality score.

## 4. Fastest route to the first paying client

1. **Week 0 (owner).** Book a solicitor (fixed fee) with [LEGAL-REVIEW-CHECKLIST.md](LEGAL-REVIEW-CHECKLIST.md).
   Buy one domain and two mailboxes and start the warm-up. The warm-up only advances on live sends, so it needs
   the solicitor's go-ahead for UK corporate subscribers first.
2. **Week 0 (owner).** Pick 10 UK IT MSPs you can reach personally. Offer the pilot from GO-TO-MARKET §6.
3. **Week 1–3.** Deploy to a VPS (`docs/DEPLOYMENT.md`). Record the solicitor's review on the Go-live page, enable
   GB and sign the checklist. Run the free sources live: `pipelineos signals check …`.
4. **Week 3–5.** Run the pilot: 100–300 people, every email approved, Pilot Mode on.
5. **Week 5.** Issue the first invoice by bank transfer. Record the result honestly, good or bad.

## 5. What I can do vs. what needs a human

| I can implement and test | Needs an external human |
|---|---|
| Emergency stop, Pilot Mode, KPIs, ROI, landing page, economics model (**done**) | Solicitor: legal position, LIA, notice, DPA, terms |
| Onboarding wizard, proof capture, seed-test tool, placement checks | Owner: domain, mailboxes, DNS, warm-up, VPS, ICO fee |
| Staging config, deployment scripts, more tests | Real customer: pilot, feedback, payment |
| Live-source checks **if** the environment's network allows those hosts | Accountant: VAT and tax rules; payment provider account |
| | Pen tester: external security test |

## 6. Improvements made in this phase

### 6.1 Emergency stop that holds work (P0)
- **Problem.** The `paused` client status existed, but nothing could set it. Had it been set, the send-time
  compliance check would have marked queued emails `blocked_at_send` and leads `rejected_by_compliance`. A pause
  would have destroyed the pipeline, so nobody would dare press it.
- **Business impact.** A client or the operator must be able to stop everything in seconds (a complaint, a wrong
  template, a bad list) without losing the work. This is a precondition for any client saying yes.
- **Change.**
  - The gateway checks for a pause **before** compliance and holds the message (`queued`; reason
    `CLIENT_PAUSED` / `CAMPAIGN_PAUSED`).
  - The pipeline leaves leads where they are.
  - The scheduler skips held work.
  - Resuming continues exactly where it stopped.
  - The operator can pause and resume clients and campaigns, with a reason.
  - **The client's admin can stop their own account** (database function `client_pause`). Only the operator
    resumes.
  - Everything is audited.
- **Files.** `backend/migrations/versions/0019_pilot_mode.py`, `backend/app/domain/tenants/controls.py`,
  `backend/app/domain/outbound/gateway.py`, `backend/app/workflows/pipeline.py`, `backend/app/api/v1/clients.py`,
  `backend/app/api/v1/operations.py`, `frontend/src/app/operator/clients/page.tsx`,
  `frontend/src/components/EmergencyStop.tsx`.
- **Tests.**
  - `tests/integration/test_pilot_controls.py` (7);
  - `tests/security/test_pilot_api.py` (5): access control, and that the app role cannot use the owner-only
    pause policy;
  - e2e: the campaign pause/resume flow and the client's stop panel, with axe checks.
- **Result.** Pass. Inbound replies and unsubscribes are still processed while paused.
- **Remaining risk.**
  - Messages already handed to a provider (`sending`) cannot be recalled.
  - A paused client gets no automatic notification to the operator yet. It is in the audit log and the Clients
    page.

### 6.2 Pilot Mode (P0)
- **Problem.** Only daily caps existed. Nothing limited how many people a new client's first campaign could reach
  in total.
- **Business impact.** A pilot has to be small and safe by default: 100 people, not "50 a day for ever".
- **Change.**
  - `campaigns.pilot_max_contacts` defaults to **100** for every new campaign. Campaigns that existed before the
    migration keep running unlimited, so a deploy never silently stops one that has already emailed more than 100
    people (changed after the code review).
  - Once reached, new first emails are **held** (`PILOT_LIMIT_REACHED`); follow-ups to people already contacted
    continue.
  - The limit check locks the campaign row, so concurrent sends can't overshoot.
  - The operator raises or removes the limit on the new **Campaigns** page (audited).
- **Tests.** Limit holds new contacts, not follow-ups; raising the limit releases them; range validation; default
  of 100.
- **Remaining risk.** None known. The limit counts dry-run sends too, so a demo run uses up pilot slots. Reset it
  on the Campaigns page.

### 6.3 Business KPIs and honest reporting (P0/P1)
- **Problem.** The report counted "contacted" but not delivered, bounced or complaints, and nothing per 100. A
  dry-run funnel looked exactly like real results.
- **Change.**
  - The report adds: emails sent, delivered, bounces, complaints and their rates; **people reached**; **per 100
    people reached** (replies, positives, booked, attended, qualified); **qualified meetings**.
  - **`data_mode`**: `dry_run` / `live` / `mixed`. The portal shows a warning on demo data.
  - **ROI estimate** from the client's own deal value and close rate, which the client admin sets in the portal.
    It is a new configuration version; signed terms are untouched.
- **Tests.** Demo-month metrics, ROI end to end, an operator can't set a client's ROI, signed terms stay signed.
- **Remaining risk.** "Qualified" uses the lead's qualification record. It is not the billing decision, which also
  needs confirmed attendance and signed terms.

### 6.4 Unit economics simulator (P1)
- `pipelineos economics simulate [--price --retainer --hourly]`, in `backend/app/economics/simulator.py`.
- It outputs:
  - pessimistic, base and optimistic funnels for 100 to 100k leads a month;
  - a seeded Monte Carlo on 1,000 synthetic leads;
  - the leads needed for €1k, €10k and €100k a month.
- Every rate is a labelled **assumption**. Results are in GO-TO-MARKET §8. Unit tests:
  `tests/unit/test_economics.py` (8).

### 6.5 Public landing page (P1)
- `/` was a redirect to sign-in. It now states the offer, how it works, what is billed, the pilot and the safety
  measures.
- It is indexable and passes WCAG 2.1 AA (e2e).
- **It claims no results, logos or testimonials**, because there are none yet.
- The pilot button appears only when `NEXT_PUBLIC_CONTACT_EMAIL` is set.

### 6.6 Data sources: live check (blocked here)
- Every free source returned **403 from this environment's network policy**: Contracts Finder, Find a Tender, TED,
  the Gazette and Greenhouse.
- That is not a fault in the sources. **SOURCE HEALTH: UNTESTED LIVE.** Nothing was mocked to stand in for it.
- Run `pipelineos signals check ted|job-board|gazette|website|officers|stream` on your PC, or allow these hosts in
  the cloud environment's network settings:
  - `www.contractsfinder.service.gov.uk`
  - `www.find-tender.service.gov.uk`
  - `api.ted.europa.eu`
  - `www.thegazette.co.uk`
  - `boards-api.greenhouse.io`
  - `api.company-information.service.gov.uk`
  - `stream.companieshouse.gov.uk`

### 6.7 Retention schedule and stop alerts (P1)
- **Problem.**
  - Personal data had no storage limit: only on-request erasure existed.
  - A client pressing their emergency stop alerted nobody.
- **Change.**
  - **Retention job.** A daily job and the command `pipelineos retention run [--apply]`, configured in
    `config/retention.yaml`.
    - It erases a person exactly as an erasure request does: the anonymisation is now one shared function. It also
      clears contact-task notes, which can name the person.
    - It acts only when every lead for that person is past the period, nobody replied or met, and nothing is in
      flight.
    - **It is off until the solicitor sets the periods.** It never deletes on a default.
  - **Alerts.**
    - `ClientStoppedSending` pages you when a client presses their stop.
    - `PilotLimitReached` tells you a pilot is full.
    - Runbook entries cover both.
- **Tests.**
  - `tests/integration/test_retention.py` (6): off by default, the period honoured, the preview doesn't erase,
    repliers and in-flight work kept, a person erased only when all their leads have expired.
  - The existing erasure tests still pass.
  - The metrics test covers both new gauges.
- **Remaining risk.** None technically. The periods themselves are a legal decision.

### 6.8 Onboarding a client without JSON or SQL (P1)
- **Problem (found while building the wizard).** Several onboarding steps had no screen and no API:
  - giving a new client a configuration;
  - setting qualification criteria or pricing;
  - adding a sending domain or an inbox;
  - creating or starting a campaign.

  So a real client could not be onboarded from the console at all. The demo only worked because the seed script
  wrote those rows directly.
- **Business impact.** Every pilot would have needed a developer.
- **Change.**
  - **Next step.** Each onboarding client's card shows its next step in the order of an onboarding call, with the
    button for it.
  - **Offer & pricing.**
    - A form for sender, offer, purpose, qualified-meeting criteria (add, edit, remove; required or optional; judged
      from the reply or the job title) and price in the client's currency.
    - A new client starts from a niche template, `config/niches/uk-it-msp.yaml`.
    - Unchanged criteria keep their ids. A changed price or criterion must be accepted again by the client.
  - **Sending domain + inbox.**
    - The domain is validated and normalised. The address must be on the domain.
    - The inbox starts warming at the warm-up start cap.
  - **Campaigns.** Created in draft in Pilot Mode, targeting the configured countries. **Start** works only for an
    active client.
  - Every step is audited.
- **Files.** `backend/app/domain/tenants/setup.py`, `backend/app/api/v1/clients.py` (setup),
  `backend/app/api/ops.py` (domain, inbox), `backend/app/api/v1/operations.py` (campaign create/start),
  `config/niches/uk-it-msp.yaml`, `frontend/src/components/OfferPanel.tsx`,
  `frontend/src/components/SendingPanel.tsx`, `frontend/src/app/operator/clients/page.tsx`,
  `frontend/src/app/operator/campaigns/page.tsx`.
- **Tests.**
  - `tests/security/test_onboarding_setup.py` (4). One goes **from an empty client to a running pilot through the
    API alone**: setup, LIA, DPA, the client's acceptance, domain, activation, campaign start. It also proves that
    a price change needs re-acceptance.
  - e2e: a brand-new client onboarded in the browser, step by step, with an accessibility check.
- **Remaining risk.**
  - The client can't onboard themselves yet; the operator runs the call.
  - Company details can only be set when the client is created.

### 6.9 Recording payments (P1)
- **Problem.** An invoice could be issued, but there was no way to record the money arriving. "Paid" existed in
  the schema but was unreachable, so payment could never be validated in the system.
- **Change.**
  - `invoice_payments`: append-only and isolated per client. A mistake is corrected with a **reversal** entry,
    never edited.
  - Recording a payment needs your authenticator code, like issuing.
  - The invoice becomes **paid** once covered, and a paid invoice can't be reversed (a refund is a credit note).
  - An invoice with money against it can't be voided.
  - Invoices show received, still owed and overdue.
  - An `InvoiceOverdue` alert is added, with a runbook entry.
  - It works with bank transfer today; a payment provider can feed the same table later.
- **Tests.**
  - Part payment, validation, reversal, paid in full, append-only enforced by the database, and client isolation.
  - The API needs the operator's code and refuses drafts.
- **Remaining risk.** No real invoice has been paid yet. VAT treatment is still unverified by an accountant.

### 6.10 Customer proof with consent (P2)
- **Problem.** There was no honest way to collect proof for the landing page and for sales.
- **Change.**
  - The client's admin writes a testimonial in the portal and chooses its scope: anonymous, named, or named with
    logo.
  - They can withdraw it at any time. A withdrawn testimonial is never shown again, but is kept as the record of
    consent. Nothing can be deleted.
  - The operator sees only testimonials that are still consented, on the new **Customer proof** page, labelled with
    their scope.
  - We can't write or edit a client's words.
- **Tests.** Access control (viewer, other client, operator), withdrawal, no deletion, and an e2e flow with an
  accessibility check.
- **Remaining risk.** None technically. There will be nothing to show until a real client is happy.

### 6.11 Security sweep, free MX check, restore drill (P1)
- **Cross-tenant sweep.** `tests/security/test_cross_tenant_sweep.py` covers the API from another client's side:
  - It calls **every API route that takes an id** with another client's real ids (leads, messages, meetings,
    invoices and the rest), and every list and report.
  - No request may succeed on another client's data, show any of it, or crash.
  - A new route without an id mapping fails the test, so future routes can't slip past it.
  - **It found two real bugs:** two 500 errors for clients not configured yet. Fixed: `ConfigMissing` now maps to
    409 everywhere.
- **Free MX check.** A published address only counts as verified if its domain actually accepts email:
  - an MX record means valid;
  - a null MX or no records means invalid;
  - an A record only means risky, and the gate doesn't send to risky.

  This prevents the commonest hard bounce at no cost. Also fixed: a case mismatch meant mixed-case published
  addresses could never verify.
- **Restore drill, executed.**
  - The encrypted backup was restored into a fresh database, with identical row counts on all 63 tables.
  - A tampered backup was refused by both the checksum and the encryption.
  - `restore.sh` now decrypts **fully before** restoring (a failure half-way through a pipe could go unnoticed), and
    drops the half-restored database it created if anything fails.

### 6.12 Staging environment and subject-line A/B testing (P1)
- **Staging.**
  - `docker-compose.prod.yml` hard-coded `ENVIRONMENT: prod`, so a staging copy would have run as production and
    skipped the staging guards. It is now `DEPLOY_ENVIRONMENT`, defaulting to `prod`, so production is unchanged.
  - There is a new `.env.staging.example`.
  - The app now **refuses to start in staging with a live email, calendar or CRM adapter**, so staging can never
    reach real people.
  - `docs/DEPLOYMENT.md` §7 describes dev, staging and production and the release flow.
- **A/B testing of subject lines.**
  - The writer always produced three subject lines and used the first.
  - Now an operator can test 2 or 3 of them (Offer & pricing). Each lead gets a fixed variant, and the report
    shows positive replies per 100 people reached by variant.
  - A two-proportion test is included, and **no winner is shown until every variant has 100 people and p < 0.05**.
  - It is off by default.
- **Tests.** The staging guard, the significance test (checked by hand: 40/200 vs 20/200 gives p = 0.0051),
  stable variants through the real pipeline, and the "not enough data" verdict.
- **Remaining risk.** Testing levers are only useful once real emails are sent.

### 6.13 Load test re-run (P2)
- The dashboards were measured against **100,013 leads**, with this phase's new queries included: the metrics
  report, Campaigns with pilot counts, testimonials, and setup.
- The worst p95 is **633 ms**, against a 2 s target. The metrics report rose from 288 to 607 ms because of the new
  KPI queries: acceptable, and worth watching.
- Evidence: `docs/loadtest-phase-17.txt`.

### 6.14 PipelineOS selling itself, and editable company details (P2)
- **Own outreach.**
  - "Set up your own outreach" on the Clients page creates the operator's single own account, as **controller**.
  - Its onboarding checklist leaves out what doesn't apply to us (DPA, client login, client signatures, pricing),
    but still requires our LIA, footer details, configuration and a verified domain.
  - The compliance engine already treated a controller correctly.
  - A starting template targets ICP #1 (`config/niches/pipelineos-self.yaml`).
  - The account is listed after the clients and labelled.
- **Company details** can be corrected later, which matters because they print in every footer.
  - Audited with before and after.
  - Only the fields sent change, so a form can't erase a VAT number by leaving it out. That gap was caught by a
    test while building it.
- **Tests.** The own-account flow end to end, including activation, the single-account rule and the reduced
  checklist. Partial update of company details.
- **Remaining risk.** Writing to prospects on our own behalf needs the same legal review as for clients.

### 6.15 Internal security review of this phase (P1)
- **What was reviewed.** A focused review of every security-relevant change in this phase:
  - the `client_pause` SECURITY DEFINER function and its owner-only RLS policy;
  - all new endpoints and the dynamic `SET` clause;
  - payments, testimonials, templates, retention and the restore script.
- **Result.** No vulnerability at high confidence. The details:
  - The client stop can only pause the caller's own active account, and can change only status and reason.
  - Every operator route is operator-only.
  - The `SET` keys are fixed field names.
  - YAML is read with `safe_load`, and the template name is never used as a file path.
- **This is not an external penetration test.** That still needs an outside tester.

### 6.16 Code review of this phase, and the fixes (P0/P1)
A correctness review of the whole branch found ten issues. All are fixed and tested:

| # | Issue | Why it mattered | Fix |
|---|---|---|---|
| 1 | **A paused client's replies were not read** (reply polling, sweep, reply time limit and kill switch only looked at active clients) | An "unsubscribe" during a pause went unprocessed; after resuming, a held follow-up could reach that person: a PECR/GDPR breach | They now run for paused clients too. Sending stays held. Referrals created during a pause wait, and are checked after resuming. Test: unsubscribe during a pause, resume, and no follow-up is sent |
| 2 | A DNS timeout looked like "this domain takes no mail" | A good published address could be rejected for ever | A resolver failure now defers the lead, which is retried on the next run. IPv6-only domains count. One DNS check per domain |
| 3 | The migration capped **existing** campaigns at 100 | A running campaign past 100 would silently stop on deploy | Only new campaigns start as pilots |
| 4 | A full pilot kept spending AI and enrichment on new leads | Cost and review time for emails that can't be sent | New leads wait while the pilot is full |
| 5 | The send loop re-counted contacts for every queued message | Send latency growing with the queue | Counted once per campaign |
| 6 | A DNS check for every address | Slow enrichment | Cached per domain (part of #2) |
| 7 | A blank company name gave a 500 | An error instead of a message | Rejected as invalid (422) |
| 8 | An own-account slug clash or a double click gave a 500 | Same | A unique slug and 409 |
| 9 | The definition of "contacted" was copied in four places | The gateway, scheduler and metrics could disagree | One shared status list |
| 10 | The client's stop reported any database error as "not active" | A client could believe sending had stopped when it hadn't | Only the function's own refusals are mapped; anything else is an error |

### 6.17 Client self-service and the placement check (P1/P2)
- **Target accounts in the portal.**
  - The client admin keeps their own list of companies to watch, up to 500. It is companies only, with no
    personal data, and can be pasted straight from a spreadsheet.
  - The free sources then watch those companies' job boards and register changes. This is the safest of the
    contactable-rate levers in GO-TO-MARKET §7, because it needs no personal data from the client.
  - Viewers can read the list but not change it. Signed criteria and pricing stay signed. The change is audited
    as "by client".
- **Setup progress.** The client sees the same onboarding checklist the operator sees.
- **Inbox placement check.** `docs/GO-LIVE-RUNBOOK.md` §C2 describes a 10-minute manual check with free tools
  before each campaign and each new mailbox.
  - It is deliberately manual. An automated seed test would need a second sending path around the outbound
    gateway, the only part of the system allowed to send.
- **Tests.** Client isolation, viewer read-only, row-level error messages, the 500-company limit, signed terms kept,
  and an e2e page check with accessibility.

### 6.18 Inbound pilot requests (P1)
- **Why.** The safest way to win a first client is for them to ask. The landing page only had a mailto link, and
  only when a contact email was configured.
- **Change.**
  - **The form.** A pilot-request form on the landing page (name, company, work email, optional message) with
    explicit consent. A hidden honeypot field and a per-IP rate limit (about 5 an hour) keep spam out.
  - **Storage.**
    - Name, email and message are **encrypted at rest**.
    - Anyone may submit, but the database lets only the operator read or update; an anonymous context reads
      nothing back.
  - **Operator side.** A new **Pilot requests** page lists the requests, with an audited status
    (new / contacted / closed).
- **Tests.**
  - Consent required, honeypot dropped, rate limit, encryption at rest, operator-only access, and no read-back
    without the operator role.
  - e2e: a visitor submits the form, and the operator sees the request and marks it contacted.
- **Remaining risk.** The form's privacy wording and the retention period need the solicitor (checklist B8).

### 6.19 Second code review (the self-service, inbound and alert additions)
Ten more findings, all fixed:
- **Lost edits.**
  - The operator's Targeting form and the client's Target accounts page could silently overwrite each other's
    list. Each save now carries the version it was opened on, and a stale save is refused (409, "reload").
  - Config saves are serialised per client, so two saves at once no longer collide on the version number.
- **A deletion promise not kept.** The consent text promised deletion, but nothing could delete a request. The
  operator can now erase one for real (audited), and the consent wording comes from one source, the API.
- **Requests falling off the list.** Unanswered requests always come first, and the alert fires only when one has
  waited more than an hour.
- **Smaller fixes.**
  - Validation errors use the row number the client sees.
  - Pasting from a spreadsheet keeps company names with commas, and a job-board column is added.
  - Clients that aren't configured yet see a clear message instead of an editor that can't save.
  - Client saves go through the same audited config path as the operator's.

## 7. New score per area (after this phase)

| Area | Before | After |
|---|---|---|
| Product | 7 | 7 |
| Onboarding | 5 | **7.5** (guided, no JSON, client self-service for target accounts and progress) |
| Lead acquisition | 4 | **4.5** (client-supplied target accounts; still untested live) |
| Data quality | 5 | **5.5** (MX check) |
| Outreach | 6 | **6.5** (A/B testing with honest statistics) |
| Deliverability | 4 | 4 |
| Compliance (technical / legal) | 8 / 0 | **8.5** / 0 (retention schedule) |
| Security | 7 | **7.5** (automated cross-tenant sweep; external test still missing) |
| Sales workflow | 6 | 6 |
| Meeting booking | 5 | 5 |
| Billing | 5 | **6** (payments, paid, overdue) |
| Analytics | 4 | **7** |
| Operations | 5 | **7** (emergency stop, stop alerts, restore drill run, staging) |
| Customer success | 3 | **5** (client stop, ROI, consented proof) |
| Scalability | 5 | **5.5** (re-measured at 100k leads: worst p95 633 ms, target 2 s; still one server) |
| Go-to-market | 1 | **5** (model, pricing, ICP, plan, landing page with inbound form, economics, own-outreach account) |
| **Overall** | **~4.0** | **5.1** |

The next big jumps are all external:
- the solicitor's review takes compliance from 0 to 6–8 on the legal side;
- warmed domains take deliverability to 6;
- a pilot takes product, outreach and analytics to 8.
