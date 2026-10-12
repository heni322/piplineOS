# ADR-0013 — Phase 5: billing, client sign-offs, client portal and reporting

**Status:** Proposed (recorded at end of Phase 5; engineering decisions taken under the delegation in ADR-0011)

| Decision | Why | Reversible |
|---|---|---|
| **A meeting is billable only when every link of the evidence chain holds:** booked by PipelineOS, attended, attendance from the calendar/CRM or the client (never from our own operators alone), qualified, against criteria the client had accepted *before* it was booked, pricing with a per-meeting fee that the client accepted, not already billed, no open or upheld dispute. Each failed rule returns a reason code shown to both sides | Billing tied to verifiable evidence is the product's promise; the operator-only attendance rule removes a conflict of interest | Rules are code + tests |
| **Client acceptance of terms is an immutable snapshot + SHA-256 hash** (qualification criteria, pricing). Changing either in config means the client must accept again; earlier meetings stay tied to the criteria hash they were qualified against | Prevents "moving the goalposts" in either direction | — |
| **Clients accept terms themselves in the portal; an operator may only record an offline acceptance with an evidence reference** (database policy enforces who signs, as whom, and how) | Acceptance must be the client's act | — |
| **Monthly invoices in arrears: set-up fee once, monthly retainer, one line per billable meeting, credits for upheld disputes on already-issued lines.** Drafts can be rebuilt any time (old draft voided, never deleted); a month with an issued invoice is closed and later changes become credits on the next one | Simple, predictable, auditable | Yes |
| **Issuing is blocked unless:** the go-live checklist is fully signed off, the tax rule has an accountant's sign-off recorded by the owner (MFA), the supplier details in `config/supplier.yaml` have no placeholders, the client accepted the current pricing, and the invoice isn't empty. Issuing itself needs an authenticator code | "Unknown → block and flag"; VAT and go-live are owner-reserved | — |
| **Invoice numbers are gapless per prefix and year, assigned only at issue** (row-locked counter). Drafts carry `DRAFT-…` | UK VAT invoices need unique sequential numbers; rolled-back drafts must not burn numbers | — |
| **Issued invoices are immutable in the database:** only issued → paid / void (with a reason); lines of a non-draft invoice can't change; nothing is ever deleted | Integrity of financial records; defence in depth beyond the API | — |
| **Only the operator or the system writes invoices** (restrictive RLS policies); clients read their own *issued* invoices and raise disputes | Least privilege | — |
| **Tax rules are selected by the most specific matching condition; no match or a tie blocks.** Arithmetic in integer minor units, half-up rounding, symmetric for credits | No floating point, no guessing | Yes (config) |
| **VAT-threshold monitor:** rolling 12-month GBP turnover from issued invoices vs the configured threshold, with 80/90% warnings until registered | Owner obligation; the number comes from config the accountant confirms | Yes |
| **Reports only show AI cost when every call has a verified price;** otherwise they say "not configured" rather than understating | Honest metrics (R15) | — |
| **Onboarding checklist gates activation:** company details, LIA, DPA, configuration with criteria, pricing in the billing currency, a client login, criteria and pricing accepted, a verified sending domain | Nothing goes out for a client that isn't set up properly | Yes |
| **No payment provider, no invoice emails** in this phase | Both are real side effects and need owner decisions (provider, wording) before go-live | Phase 6 |
