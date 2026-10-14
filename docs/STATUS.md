# Where PipelineOS stands (2026-10-14)

Everything runs in **dry-run**. Nothing is sent, booked, written to a CRM or paid for, and no country or go-live
item has been signed off. The live view of this list is on the **Go-live** page:
- **Before you switch on:** what is done and what is not.
- **Countries:** what is built for each country, what is still needed, and who acts.

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

- **Tests:** 699 backend and 12 browser tests pass, in the cloud and on your PC.

## Still needed: yours only
1. **Legal.**
   - A solicitor reviews the UK rules, footer, privacy notice, LIA and DPA templates.
   - A local lawyer does the same for each other country. Questions for counsel are in each phase report.
   - The ICO registration and fee.
2. **Tax.** An accountant verifies the tax rules (VAT) on the Billing page.
3. **Company details** in `config/supplier.yaml`: legal name, number, address, VAT and bank details.
4. **Infrastructure.** Hosting, domain, keys, backups and monitoring, following `docs/DEPLOYMENT.md`.
5. **Providers and spending.**
   - Microsoft 365 or Google Workspace mailboxes, warmed up.
   - Choose the enrichment and verification providers.
   - A paid AI API key and spend cap for live use.
   - A paid register for Spain, Italy, Portugal and the Netherlands, if you want them.
6. **Free registers.**
   - The Companies House key.
   - Sweden and Denmark credentials.
   - The Belgium and Ireland file imports.
7. **Native speakers** check the fixed texts for each non-English country.
8. **Per client.** Onboarding checklist, LIA and DPA signed, criteria and pricing accepted, a verified sending
   domain, and an EU representative when targeting the EU/EEA from outside it.
9. **Go-live.** Sign each checklist item, enable each country, then set `GLOBAL_GO_LIVE=true` yourself
   (`docs/GO-LIVE-RUNBOOK.md`).

## Code
- The work is on stacked branches up to `phase/9-nordics-ready`; `main` still holds only the initial commit.
- Merging into `main` (by pull request, no history rewritten) is your call, whenever you are happy with the review.
