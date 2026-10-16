# Legal review checklist (for the solicitor)

**Status: nothing on this list has been reviewed by a lawyer.** "Technically implemented" means the system
enforces it in code and tests. It does **not** mean it is lawful. Every row marked `LEGAL REVIEW REQUIRED` needs a
written answer before the first real email. Record the review on the Go-live page (Countries → counsel
verification); the system will not enable a country until you do.

## A. The basis for writing to people

| # | Question for counsel | Technically implemented | Legally validated |
|---|---|---|---|
| A1 | UK PECR reg. 22: may unsolicited marketing email go to **corporate subscribers** (Ltd, LLP, PLC) without prior consent, and are sole traders and partnerships correctly treated as individuals? | Subscriber-type check from the register; non-corporates blocked or moved to other channels | `LEGAL REVIEW REQUIRED` |
| A2 | UK GDPR Art. 6(1)(f): is the legitimate-interests assessment template adequate per client (processor) and for our own outreach (controller)? | LIA reference and approval date are required before activation | `LEGAL REVIEW REQUIRED` |
| A3 | Art. 14: is the privacy notice content adequate (source, purpose, basis, rights, retention), and is "link in the first email" timely? | Notice page per client, linked in every footer; **the text is a placeholder** | `LEGAL REVIEW REQUIRED` |
| A4 | Use of **Companies House officer names** to identify decision-makers (no email ever guessed) | Name, occupation and appointment only; no date of birth or address | `LEGAL REVIEW REQUIRED` |
| A5 | Use of **published business addresses** from the company's own website, verified by company number | Only when the site cites the company number | `LEGAL REVIEW REQUIRED` |
| A6 | Each EU country in `config/countries/`: are the rules files right? Germany and Austria are email-off by default | Rules engine per country, `verified_by_counsel: false` everywhere | `LEGAL REVIEW REQUIRED` |
| A7 | Art. 27 EU representative needed when writing into the EU from the UK? | Recorded per client; activation needs it where flagged | `LEGAL REVIEW REQUIRED` |

## B. Running the service

| # | Question | Technically implemented | Legally validated |
|---|---|---|---|
| B1 | Controller/processor split: the client is controller of its campaigns, we are processor. Is the DPA (Art. 28) adequate? | DPA status/version/date recorded; **no DPA document in the repo** | `LEGAL REVIEW REQUIRED` |
| B2 | Sub-processors list (hosting, Microsoft/Google mailboxes, HubSpot if used) and international transfers | `config/transfers.yaml`, sub-processor table | `LEGAL REVIEW REQUIRED` |
| B3 | Objections and unsubscribes: honoured across all clients (global suppression). Is cross-client suppression lawful and correctly described? | Global suppression by keyed hash; one-click unsubscribe (RFC 8058) | `LEGAL REVIEW REQUIRED` |
| B4 | Data-subject requests: access, erasure, objection workflow and the 1-month deadline | DPR queue with deadlines; erasure keeps only hashes needed for suppression | `LEGAL REVIEW REQUIRED` |
| B5 | Retention periods: how long to keep people never emailed, and people emailed who never replied? How long for people who replied, met or were billed (invoice and dispute evidence)? | Daily retention job built (`config/retention.yaml`, `pipelineos retention run`); erases exactly like an erasure request. **Off: every period is null until you set it.** Repliers, meetings and billing records are never erased by it | `LEGAL REVIEW REQUIRED` (the periods) |
| B6 | AI disclosure in replies ("drafted with the help of AI") is enough and in the right place? | Always added (configurable) | `LEGAL REVIEW REQUIRED` |
| B8 | Landing-page pilot-request form: is the consent wording right, what privacy information must sit next to it, and how long do we keep a request that never becomes a client? | Explicit consent box; encrypted storage; operator-only access; no retention period set yet | `LEGAL REVIEW REQUIRED` |
| B9 | Reading watched companies' own careers pages (ADR-0020): job ads published as schema.org JobPosting, on sites the client named. Is this compatible with website terms of use, database right and copyright for our purpose, and is anything about it a personal-data processing we must declare? | Company data only (title, place, dates, ad URL); descriptions, salaries and contact points never read; robots.txt obeyed (RFC 9309), identified bot, one request per site every 2 s at most, a few pages a day per site; ads unseen for 180 days deleted | `LEGAL REVIEW REQUIRED` |
| B7 | ICO registration (data protection fee) for the operator | n/a | **Owner action** |

## C. Commercial terms

| # | Question | Technically implemented | Legally validated |
|---|---|---|---|
| C1 | Terms of service between us and the client (liability cap, warranties, termination, the client's own lawful-basis duties) | **Not drafted** | `LEGAL REVIEW REQUIRED` |
| C2 | The **billable meeting definition**: system-booked, attended (confirmed by calendar, CRM or client), qualified against criteria the client signed before booking, pricing signed, no open or upheld dispute | Enforced in `app/domain/billing/eligibility.py`; client signs criteria and pricing in the portal (logged) | `LEGAL REVIEW REQUIRED` (is the portal acceptance a binding signature?) |
| C3 | Dispute window and process (default 14 days) | Built | `LEGAL REVIEW REQUIRED` |
| C4 | Pilot guarantee and refund wording (GO-TO-MARKET §2) | n/a | `LEGAL REVIEW REQUIRED` |
| C5 | Invoices: VAT treatment (UK B2B, EU reverse charge) | Tax rules in `config/tax_rules.yaml`, **not checked by an accountant** | **Accountant** |
| C6 | Landing-page claims | No results or testimonials claimed | `LEGAL REVIEW REQUIRED` (light) |

## D. What to bring to the meeting

- `docs/05-compliance-engine.md`: how decisions are made.
- `config/countries/GB.yaml`: the UK rules as coded.
- `config/footers/`: the email footer texts.
- The privacy-notice placeholder (`/privacy/<client>`).
- This checklist, with an answer per row.
