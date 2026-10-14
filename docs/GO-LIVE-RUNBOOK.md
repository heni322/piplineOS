# Go-live runbook (Phase 7)

Go-live is the owner's decision and action. Nothing in this repository turns on real sending by itself. Three independent keys must all be on, and the database refuses a live send unless the go-live checklist is fully signed off.

## A. Before go-live (owner, outside the system)
1. **Solicitor review.** Covers:
   - compliance configuration in `config/countries/*`
   - email footer
   - privacy notice
   - LIA template
   - DPA
   - data-protection response templates
   - AI disclosure wording
   - what is kept after erasure
   - the reply-safety keyword lists
2. **ICO data protection fee** paid; registration number recorded.
3. **Transfer documents** for operator access from outside the UK (IDTA / addendum, transfer risk assessment).
4. **Accountant:**
   - VAT status and threshold monitoring
   - invoice wording
   - the tax rules in `config/tax_rules.yaml`
5. **Your company details** in `config/supplier.yaml` (no `TODO` left). Real pricing agreed with each client.
6. **Microsoft 365 setup:**
   - an app registration with Mail.Send, Mail.Read and Calendars.ReadWrite application permissions
   - an **Exchange application access policy** restricting it to the sending mailboxes only
   - its tenant ID, client ID and secret put in `.env.prod`

   **Or Google Workspace,** for a client who sends from Google:
   - a service account with domain-wide delegation, authorised in the Workspace admin console for `gmail.send`, `gmail.readonly`, `calendar.events` and `calendar.freebusy` only
   - a Pub/Sub topic that Gmail may publish to, and a push subscription to `https://APP_DOMAIN/webhooks/google` with authentication turned on
   - the key and those names in `.env.prod` (`GOOGLE_*`); set each Google inbox's provider to `google`
7. **Sending domains:**
   - separate from your main domain
   - SPF, DKIM and DMARC published and verified
   - mailboxes warmed up over 3–4 weeks at low volume (daily caps start at 30)
8. **Companies House API key** (free) in `.env.prod`. Set `COMPANIES_HOUSE_ADAPTER_MODE=live`; this is read-only.
   - **Other countries' registers, only when that country is about to be enabled:**
     - France, Norway and Finland need no key: set `FR_`, `NO_` or `FI_REGISTRY_ADAPTER_MODE=live`.
     - Sweden: register for Bolagsverket's free API (värdefulla datamängder), then set `BOLAGSVERKET_CLIENT_ID` / `_SECRET`.
     - Denmark: ask Erhvervsstyrelsen (cvrselvbetjening@erst.dk) for free system-to-system access, then set `CVR_USERNAME` / `CVR_PASSWORD`.
     - For Sweden and Denmark, first set the mode to `sandbox` and look up one known company. The adapters were built from the published field names and have not yet seen the real service.
9. **Enrichment and email-verification providers** chosen and contracted. Their adapters are built only after you choose (see the Phase 6 report).
10. Backups running, a restore drill passed, monitoring and alerts wired, keys stored offline.

## B. In the system (owner, with your authenticator code)
1. **Accountant's review of each tax rule:** Billing → tax rules, or `POST /api/v1/tax-rules/{code}/accountant-verification`.
2. **Sign off each go-live checklist item** (Governance). Each sign-off asks for your authenticator code and an evidence link.
3. **Enable the country** once counsel has verified its rules. GB was enabled only in demo databases.
4. **For each client:**
   - onboarding checklist complete (company details, LIA, DPA, configuration, pricing, client login, criteria and pricing accepted by the client, a verified sending domain)
   - then **Activate**
5. **Approval mode stays "always"** for at least the first two weeks; the database enforces this.

## C. Switching on (owner)
1. In `.env.prod`:
   - `EMAIL_ADAPTER_MODE=live`
   - `CALENDAR_ADAPTER_MODE=live`
   - `GLOBAL_GO_LIVE=true`

   Then restart. Expect the `GoLiveSwitchedOn` alert.
2. Set **one** client to live mode, the first pilot client.
3. Approve a single email to an address you control at that client and confirm all of the following:
   - it arrives
   - the footer and one-click unsubscribe work
   - the unsubscribe suppresses the address
   - `pipelineos_sends_last_hour{mode="live"}` shows 1
4. Watch the first week daily:
   - bounce and complaint rates (the kill switch pauses an inbox automatically)
   - replies and escalations
   - the data-protection tracker

## D. Rolling back
- **Immediate stop, everything:** set `GLOBAL_GO_LIVE=false` and restart. Every send falls back to dry-run.
- **One client:** turn that client's live mode off.
- **One inbox:** it pauses itself on bad deliverability. You can also pause it, and resume it after fixing the cause (with a written reason).
