# Where PipelineOS stands (2026-10-15)

Everything runs in **dry-run**. Nothing is sent, booked, written to a CRM or paid for, and no country or go-live
item has been signed off. The live view of this list is on the **Go-live** page:
- **Before you switch on:** what is done and what is not.
- **Countries:** what is built for each country, what is still needed, and who acts.

## Business readiness (Phase 17)
**5.1 / 10.** The blockers are mostly outside the code: a solicitor's review, a sending domain and warmed
mailboxes, a first pilot client, and payment. See `docs/BUSINESS-READINESS.md`, `docs/GO-TO-MARKET.md` and
`docs/LEGAL-REVIEW-CHECKLIST.md`.
- **Emergency stop** that holds work: the operator pauses a client or campaign, or the client stops their own
  account. Nothing is cancelled, and resuming continues where it stopped.
- **Pilot Mode:** every campaign emails at most 100 new people until the operator raises the limit (Campaigns page).
- **Reports per 100 people reached:** deliverability, qualified meetings, a client ROI estimate, and a clear label
  on dry-run numbers.
- **Onboarding from the console:** offer, criteria and pricing (from a niche template), sending domain, inbox and
  campaign, with a guided next step for each client.
- **Payments:** record money received against an invoice (your authenticator code; reversals, never edits); paid
  and overdue tracking.
- **Retention schedule**, off until the solicitor sets the periods (`config/retention.yaml`).
- **Public landing page** at `/`, and `pipelineos economics simulate` for the unit economics.

**Your next steps, ready to use:**
- `docs/LAUNCH-PLAN-30-DAYS.md`: the next 30 days, step by step.
- `docs/SOLICITOR-BRIEF.md`: the email and documents for the legal review.
- `docs/FIRST-CLIENT-KIT.md`: how to win the first pilot.

## Built
- **The five agents and the compliance gate.** Finding signals, finding decision-makers, writing, handling
  replies and booking, orchestrated together. Every send goes through the compliance gate, the suppression check
  and the three-key switch (live provider, live client, global go-live).
- **Operator console and client portal.** Approvals, inboxes and sending domains, leads (personal data masked),
  data-protection requests, escalations, billing, CRM sync, governance and go-live.
- **Real-provider adapters**, all off or in dry-run until you switch them on:
  - Microsoft 365 and Google Workspace (email, calendar, replies);
  - HubSpot;
  - Hunter (paid, behind the paid-call gate);
  - UK public-contract notices;
  - a local AI model for testing.
- **Go-live preparation.** Sending-domain DNS checks, mailbox warm-up, the readiness list, backups and restore,
  monitoring and alerts.
- **Replies safety net.** Replies whose push notification was lost are found within 15 minutes and handled once.
- **Free-only mode** (`FREE_ONLY=true`, Phase 11). No paid provider is ever called:
  - **triggers:** public contract awards, plus each client's watchlist: its public job boards (Greenhouse, Lever,
    Ashby) and new directors on the Companies House register;
  - **decision-makers:** the free Companies House officers list. There's no email address, so each lead becomes
    a human "published contact" task;
  - **AI:** the free local model.
- **A stronger free search** (Phase 12):
  - **funding triggers for the whole UK market** from Companies House filings, chosen by each client's SIC codes;
  - **EU contract awards** (TED);
  - an **insolvency filter** (the Gazette and the register);
  - the **company's own published business address**, taken only from a website proven to be theirs by its
    company number.
  - Check each live source once on your PC: `pipelineos signals check …` (`docs/PHASE-12-REPORT.md` §5).
- **Source health** (Phase 15): each signal source's runs, failures and last success on the Go-live page, with an
  alert when one keeps failing. One broken source or client no longer stops the hunt.
- **Targeting form** (Phase 14): each client's keywords, SIC codes, countries and watchlist, set on the Clients
  page.
- **Contact tasks** (Phase 13): the operator's queue of leads to reach by hand, with names masked until a logged
  reveal, and each task closed once with a note.
- **Countries.**

  | Situation | Countries |
  |---|---|
  | Built; register free with no setup | UK (free key), France, Norway, Finland |
  | Built; register free but needs your action | Sweden, Denmark (apply for credentials); Belgium, Ireland (download and import the file) |
  | Built (texts, checks, reply safety) but no free register | Spain, Italy, Portugal, Netherlands |
  | Email needs prior consent: other channels only | Germany, Austria (by the current rules) |

- **Reply safety in every built language**, also when the AI is wrong. With the free local model (Qwen 2.5 7B)
  on your PC, replies were understood correctly at these rates:

  | Replies in | Correctly understood |
  |---|---|
  | English | 98.4% |
  | Spanish, Italian, Portuguese, Dutch | 98.1% |
  | Nordic languages | 95.4% |
  | French | 90.5% |

- **Tests:** 749 backend and 14 browser tests pass in the cloud, and GitHub CI passes all four jobs (Phase 16).

## Still needed: yours only
1. **Legal.**
   - A solicitor reviews the UK rules, footer, privacy notice, LIA and DPA templates.
   - A local lawyer does the same for each other country. Questions for counsel are in each phase report.
   - The ICO registration and fee.
2. **Tax.** An accountant verifies the tax rules (VAT) on the Billing page.
3. **Company details** in `config/supplier.yaml`: legal name, number, address, VAT and bank details.
4. **Infrastructure.** Hosting, domain, keys, backups and monitoring, following `docs/DEPLOYMENT.md`.
5. **Providers and spending.**
   - Microsoft 365 or Google Workspace mailboxes, warmed up. This is the one service that can't be free.
   - With `FREE_ONLY=true`, nothing else to buy. Without it: enrichment and verification providers, and a paid
     AI key with a spend cap.
   - A server able to run the local model: 32 GB RAM for the recommended `glm-4.7-flash` (about $168 a month
     on DigitalOcean), 16 GB for a smaller one. Setup, model choice and the benchmark: `docs/LOCAL-LLM.md`.
   - Spain, Italy, Portugal and the Netherlands stay off: they have no free register.
6. **Free registers.**
   - The Companies House key.
   - Sweden and Denmark credentials.
   - The Belgium and Ireland file imports.
7. **Native speakers** check the fixed texts for each non-English country.
8. **Per client.** Onboarding checklist, LIA and DPA signed, criteria and pricing accepted, a verified sending
   domain, and an EU representative when targeting the EU/EEA from outside it.
9. **Go-live.** Sign each checklist item, enable each country, then set `GLOBAL_GO_LIVE=true` yourself
   (`docs/GO-LIVE-RUNBOOK.md`).

## Live check of the free sources (2026-10-19)
Run from your PC: TED, Find a Tender, the job boards and the France, Norway and Finland registers work. Wikidata
needed a contact in our User-Agent (fixed). Contracts Finder returns nothing; the Gazette blocks the PC's network.
**Measured contactable rate with free sources only: 0 of 40 real award winners.** The client's own target accounts
are now the main route; see `docs/LIVE-SOURCE-CHECK.md`.

## Free local AI on the server (2026-10-19)
The production stack now includes the model server (Ollama) as an optional service with no internet access and
no published port. `pipelineos llm benchmark` compares candidate models on the server (reply safety and accuracy in
every language, and email drafting) and recommends the best fully safe one. Candidates by server memory:
`gemma4:12b` (16 GB), `glm-4.7-flash` or `gemma4:26b` (32 GB), `qwen3.6:35b` (48 GB+), `qwen3.6:27b` (GPU). See
`docs/LOCAL-LLM.md`.

## Google Places, Gemini and the voyz.fr server (2026-10-19)
- **Google Places** (paid, behind the three keys, capped at 1,000 searches a month = Google's free allowance):
  suggests a company's website when the free sources know none (the 0-of-40 gap), and finds new companies by
  activity and area (`pipelineos places discover`). Each site must still prove it is the company's by printing a
  register number the register confirms; only its generic business address is used. Nothing from Google is
  stored. See `docs/GOOGLE-PLACES.md`.
- **Gemini** as an optional AI provider (`LLM_PROVIDER=gemini`): paid, only with the three keys, never with
  `FREE_ONLY=true`, paid tier only (the free tier lets Google use prompts). The default stays the free local model.
- **voyz.fr** on the shared Contabo VPS (France): `deploy/vps/` (secrets generated on the server, memory caps,
  behind the existing nginx) and `docs/DEPLOY-VPS.md`.

## Code
- Phases 2–17 are on `main` (GitHub pull requests up to #19).
- The live-check fixes, the local AI server, Google Places, Gemini and the VPS deployment are on branch
  `fix/live-source-findings`, ready for a pull request.
