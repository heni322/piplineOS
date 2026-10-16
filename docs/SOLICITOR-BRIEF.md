# Solicitor brief: ready to send

This unlocks the largest part of the score: legal validation goes from 0 to 6–8, and the overall score from about
5 to about 6.

## 1. Who to ask
- A UK solicitor or firm that does **data protection and marketing law** (PECR and UK GDPR).
  - Search terms: "PECR solicitor", "data protection solicitor B2B marketing".
  - The Law Society's "Find a Solicitor" lets you filter by *data protection*.
- Ask for a **fixed fee** for a scoped review. A review of this size is typically quoted somewhere between a few
  hundred and a couple of thousand pounds. Get two quotes.
- Attach: this brief, `docs/LEGAL-REVIEW-CHECKLIST.md`, `docs/05-compliance-engine.md`, `config/countries/GB.yaml`.

## 2. Email to send (copy, fill the brackets)

> **Subject:** Fixed-fee review: B2B outreach service, PECR / UK GDPR
>
> Hello [name],
>
> I run [company], a UK start-up that books first meetings for UK IT service companies. We find a public buying
> signal (a contract award, new roles, a new director), write one short email to the relevant decision-maker at
> the company, and book a meeting if they're interested. We write only to **corporate subscribers** (limited
> companies, LLPs, PLCs). Sole traders and partnerships are excluded automatically. Every email carries a
> one-click unsubscribe, and objections are honoured across all our clients.
>
> Before our first pilot I'd like a fixed-fee review of our legal position. The attached checklist lists 21
> specific questions. The main ones are:
> 1. PECR reg. 22 for corporate subscribers, and our subscriber-type checks;
> 2. our legitimate-interests assessment template and Article 14 privacy notice;
> 3. using Companies House officer names to identify decision-makers;
> 4. the controller/processor split and our DPA with clients;
> 5. retention periods;
> 6. our client terms, including the definition of a billable meeting and a pilot refund.
>
> Could you quote a fixed fee and a timescale? I can send anything else you need, and a short call would help.
>
> Kind regards, [name, phone]

## 3. What you need back, in writing
- An answer for each row of the checklist (A1–C6, B8): **OK as is**, **change X**, or **don't do this**.
- Approved texts:
  - the privacy notice (`/privacy/<client>`);
  - the email footer;
  - the LIA template;
  - the DPA;
  - the client terms, including the pilot refund wording;
  - the pilot-request form wording;
  - the retention periods.

## 4. What I do when the answers arrive
Tell me, and I'll do these in one sitting, all tested:
1. **Record the review on the Go-live page.** The system then lets you enable GB sending.
2. **Put the approved texts in place:** notice, footer, form consent, terms.
3. **Set the retention periods** in `config/retention.yaml`. The deletion job then starts.
4. **Apply any "change X" answers** to the rules in `config/countries/GB.yaml` and the compliance engine, with
   tests.
