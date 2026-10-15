# PipelineOS: business readiness (2026-10-15)

> Companion documents: [GO-TO-MARKET.md](GO-TO-MARKET.md) (model, pricing, ICP, pilot plan, unit economics, risks)
> and [LEGAL-REVIEW-CHECKLIST.md](LEGAL-REVIEW-CHECKLIST.md) (what the solicitor must review).

## 1. Verdict

**Business readiness: 4.5 / 10.** It was about 4.0 before this phase.

PipelineOS is a technically strong system:
- 760+ backend tests, 14 browser tests with WCAG checks, strict typing;
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
| Backups restored and tested in production | **PARTIAL**: scripts and a restore test exist; no production server yet |
| External security test | **MISSING**: internal tests, scans and threat model only |

## 2. Audit

Scores are 0–10 and evidence-based. "Built" means implemented and tested on synthetic data in dry-run.

| Area | Current state | Target (9/10) | Gap | Impact | Priority | Score |
|---|---|---|---|---|---|---|
| Product (core pipeline) | 5 agents, compliance gate, approvals, replies, booking; all dry-run | Proven on real prospects | No real run | High | P0 (external) | 7 |
| Onboarding | Operator checklist; client signs criteria and pricing in the portal; targeting form | Guided wizard; client self-onboards in under 1 hour | No wizard; operator-driven | Medium | P1 | 5 |
| Lead acquisition | Free sources: UK/EU contract awards, Companies House filings and officers, job boards, websites, insolvency filter | Measured leads/week per ICP | **Not tested live from this environment** (network policy); free registers rarely give an email | **High** | P0 | 4 |
| Data quality | Register-verified companies; website proven by company number; data minimisation | Measured accuracy and bounce rate | No measurement | High | P1 | 5 |
| Outreach | Writer with sourced claims, critique, rewrites, quality score 0–10, human approval of every email | Reply rate measured and improved | Evals are synthetic | High | P1 | 6 |
| Deliverability | DNS check (SPF/DKIM/DMARC), warm-up, inbox ≤50/day, campaign caps, automatic kill switch (bounce 3%, complaint 0.3%), business hours, one-click unsubscribe | Seed-tested placement on warmed domains | No domain, no mailbox, no placement test | **High** | P0 (external) | 4 |
| Compliance: technically implemented | Engine at queue and send time, global suppression, per-country rules, DPR handling, audit log, AI disclosure | n/a | n/a | n/a | n/a | 8 |
| Compliance: legally validated | Nothing verified by counsel; GB rules flagged unverified | Solicitor's sign-off recorded | **Everything** | **Blocking** | P0 (external) | 0 |
| Security | RLS forced, MFA, CSRF, encrypted PII, blind indexes, append-only audit, gitleaks/trivy in CI, threat model | External penetration test | No external test | High | P1 (external) | 7 |
| Sales workflow (replies) | Classification with confidence; safety classes always go to a human; objection playbook | Accuracy measured on real replies | Local-model accuracy measured on synthetic replies only (98.4% English) | Medium | P1 | 6 |
| Meeting booking | Calendar adapters (M365/Google), slots, reminders, attendance events | Real bookings | Dry-run only | Medium | P1 | 5 |
| Billing | Billable meeting = system-booked + attended (confirmed by calendar/CRM/client) + qualified against SIGNED criteria + signed pricing + no dispute; evidence chain; dispute window; invoice drafts | An invoice issued and paid | No payment provider; tax rules not checked by an accountant | High | P1 (external) | 5 |
| Analytics | **New:** delivered, bounces, complaints, KPIs per 100 people reached, qualified meetings, dry-run flag, client ROI estimate | Measured against a pilot | No real data | Medium | done/P1 | 7 |
| Operations | Backups and restore scripts, Prometheus metrics, alerts, runbooks, **new emergency stop that holds work** | Staging and production, restore drill done | No staging or production server | High | P1 | 6 |
| Customer success | Portal: results, meetings with evidence, disputes, CRM, **new emergency stop and ROI** | Proof capture, reviews, renewal | No proof or case-study workflow | Medium | P2 | 4 |
| Scalability | Celery workers, per-tenant caps, load test (Phase 6) | Proven at 10+ clients | Single server design | Low now | P2 | 5 |

**Overall: 4.5 / 10.** This is the weighted view: the four "MISSING" rows of section 1 dominate.

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
| 12 | Payment collection (bank transfer first; Stripe/GoCardless later) and accountant check of tax rules | P1 | **Accountant, payment provider** | open |
| 13 | Unit-economics model to set prices and pilot size | P1 | me | **DONE (this phase)** |
| 14 | Public landing page | P1 | me | **DONE (this phase)**; needs a domain and contact email |
| 15 | Client onboarding wizard (self-serve) | P1 | me | open |
| 16 | External penetration test | P1 | **Pen tester** | open |
| 17 | Inbox-placement (seed) test before each campaign | P1 | me + owner (seed mailboxes) | open |
| 18 | Customer proof capture (consent to quote results) | P2 | me | open |
| 19 | PipelineOS selling itself (operator's own tenant) | P2 | owner + solicitor | architecture supports it (`is_operator_own`) |
| 20 | Automatic data-retention schedule (only on-request erasure exists) | P1 | solicitor sets periods, then me | open |

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
  - `campaigns.pilot_max_contacts` defaults to **100** for every campaign, existing ones included.
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

## 7. New score per area (after this phase)

| Area | Before | After |
|---|---|---|
| Product | 7 | 7 |
| Onboarding | 5 | 5 |
| Lead acquisition | 4 | 4 |
| Data quality | 5 | 5 |
| Outreach | 6 | 6 |
| Deliverability | 4 | 4 |
| Compliance (technical / legal) | 8 / 0 | 8 / 0 |
| Security | 7 | 7 |
| Sales workflow | 6 | 6 |
| Meeting booking | 5 | 5 |
| Billing | 5 | 5 |
| Analytics | 4 | **7** |
| Operations | 5 | **6** (emergency stop) |
| Customer success | 3 | **4** (client stop, ROI) |
| Scalability | 5 | 5 |
| Go-to-market | 1 | **4** (model, pricing, ICP, plan, landing page, economics) |
| **Overall** | **~4.0** | **4.5** |

The next big jumps are all external:
- the solicitor's review takes compliance from 0 to 6–8 on the legal side;
- warmed domains take deliverability to 6;
- a pilot takes product, outreach and analytics to 8.
