# Phase 9.1 — Stage 2: Sweden and Denmark: Report (2026-10-14)

## 1. Summary
With this phase, all four Nordic countries have a company register adapter. Sweden and Denmark stay **disabled**:
- like every country, they need a lawyer's review before they can be enabled;
- their registers, although free, need access credentials that only you can apply for.

- **Swedish company register (Bolagsverket).** Free API under the EU high-value-datasets rules. It needs an OAuth
  client issued on registration.
  - Lookups are by organisation number only, because the API has no name search. A Swedish company without a
    known number stays "unknown", which blocks.
  - A sole trader in Sweden is identified by a personal identity number. The adapter recognises one before any
    call and **never sends it**, returning "unknown".
- **Danish company register (CVR).** Erhvervsstyrelsen's free system-to-system access, with a user ID and
  password issued on request.
  - Lookups by CVR number or exact name.
  - Each search asks for only four fields: number, name, legal-form code and status. Owners and board members
    are never requested.
  - Credentials are only ever sent over HTTPS; a plain-HTTP address is refused.
- **Both registers:**
  - Sole traders count as individuals and are never emailed.
  - Bankrupt, liquidating or closed companies count as "unknown", which blocks.
  - Each adapter fails loudly (`AdapterNotConfigured`) if it is switched on without credentials.
- **Danish replies.** The Nordic test set gained 14 Danish replies. The safety rules missed them almost entirely
  (0 of 4 unsubscribes, 0 of 2 objections, 1 of 2 data requests). The Danish hidden instruction was not flagged,
  and a model that always answers "interested" would have acted on it. All Danish replies are now caught.
- **Corrections.**
  - The Swedish and Danish country files listed EUR as the currency; they now say SEK and DKK.
  - Danish consumer mail domains, Swedish and Danish places, and the Copenhagen time zone were added.

## 2. Results

| Check | Result |
|---|---|
| Backend tests (cloud) | **663 passed** (Phase 9: 636) |
| Lint, types, 3 import contracts | Clean |
| Nordic set (74 replies: Norwegian, Danish, Finnish, Swedish, English), rules only, also with an always-"interested" model | Unsubscribe, objection, data request: 100%; 9 of 9 hidden instructions escalated, 0 acted on |
| English and French sets | Unchanged |
| Nordic replies with Qwen 2.5 7B on the owner's PC | **95.4%**; safety 100%; 9 of 9 injections escalated (§2.1) |
| On the owner's PC | **663 passed, 0 failed, 0 skipped** (§2.2) |

**Limitation, stated plainly:** the Swedish and Danish adapters were written from the registers' published field
names and tested against recorded examples, not against the live services. No credentials exist yet. The go-live
runbook (A.8) now requires a sandbox lookup of one known company before either country is enabled.

### 2.1 Nordic replies with a real model (Qwen 2.5 7B on the owner's PC, 74 replies, ≈ 15 s each)
- **95.4%** correctly classified, up from 94.2% on the 60-reply set. All 14 new Danish replies were right.
- Unsubscribe, objection and data request: 100%. Out of office, not now and referral: 100%.
- Hidden instructions: 9 of 9 escalated, 0 acted on.
- The three mistakes are the same Finnish/"?" ones as in Phase 9; none is unsafe.

### 2.2 On the owner's PC
- Synced on branch `phase/9-nordics-ready`. Country files re-synced: Sweden SEK, Denmark DKK, as new versions;
  nothing was enabled.
- Backend against the PC's Docker PostgreSQL: **663 passed, 0 failed, 0 skipped** (≈ 474 s).

### 2.3 No external side effects
- All register calls in the tests go through an in-process mock transport.
- Register modes default to `mock` and are forced to `mock` in tests.
- No credentials were created or stored, no country was enabled and nothing was signed.
- No data, table, file or branch was deleted.

## 3. What you do (only when you want Sweden or Denmark)
1. **Sweden:** register for Bolagsverket's free API ("värdefulla datamängder"). Put the client ID and secret in
   `.env.prod`.
2. **Denmark:** email cvrselvbetjening@erst.dk for free system-to-system CVR access. Put the user ID and password
   in `.env.prod`.
3. Run one sandbox lookup each (runbook A.8).
4. Get the lawyer's review. Questions for counsel:
   - **Denmark:** our understanding is that the Danish Marketing Practices Act (§10) may require prior consent for
     email marketing to businesses as well as to people. If so, Denmark becomes "consent required" and is never
     emailed, only contacted by other channels.
   - **Denmark:** should partnership companies (P/S, often law and accounting firms) be treated as companies? They
     are currently treated cautiously, as individuals.
   - **Sweden:** is B2B email to a legal person's staff opt-out under the Marketing Act (§19)?
5. Record each review on the Go-live page and enable the country with your authenticator code.

## 4. Next
- Ireland (CRO) and the Netherlands (KvK): both registers charge for access. Your decision.
- Stage 4 (Spain, Italy, Belgium, Portugal), following the France pattern.
