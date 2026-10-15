# Phase 11 — Free sources only: Report (2026-10-14)

## 1. Why
The owner's rule: **no paid API**. Before this phase, three steps depended on paid services:
- finding the decision-maker and their email (Hunter);
- verifying the email (Hunter);
- the AI in live mode (the paid Claude API).

The original idea also proposed scraping LinkedIn to find the decision-maker and reading their posts. That was
**not built**, and should not be:
- LinkedIn's terms forbid scraping.
- Scraping personal profiles of UK/EU prospects has no lawful basis we could defend under UK GDPR / GDPR.
- This product sells compliance-first outreach. One complaint about scraped data would undo it.

So this phase replaces each paid or scraped step with a **free and lawful** source.

## 2. The free search stack

| Step | Source | Cost | What it finds |
|---|---|---|---|
| Agent 1 — triggers | Contracts Finder and Find a Tender award notices (existing) | free, no key | companies that just won public contracts |
| Agent 1 — triggers | **Public job boards** of watched companies: Greenhouse, Lever (incl. EU), Ashby (new) | free, no key | companies hiring in the client's area (`icp.hiring_keywords`) |
| Agent 1 — triggers | **Companies House officers** of watched UK companies (new) | free key | "Company X just appointed a new CTO / director" |
| Agent 2 — decision-maker | **Companies House officers** (new, `FREE_ONLY`) | free key | the current directors, by name and stated occupation |
| Agent 2 — legal form | Companies House and the free EU registers (existing) | free | corporate or individual subscriber |
| Agent 3 — personalisation | the signal itself (the award notice, the job ads, the appointment) | free | the reason to write now |
| AI (all agents) | the local model, Qwen 2.5 7B via Ollama (existing; now also live with `FREE_ONLY`) | free | scoring, drafting, reply handling |

**The watchlist.** Each client can name target accounts in its configuration (`watchlist`, up to 500), for
example:

```json
{"name": "Contoso Managed IT Ltd", "company_number": "01234567", "job_board": "greenhouse:contoso"}
```

Job boards and register changes are watched only for those companies. Award notices still cover the whole market.

## 3. What changed
- **`app/adapters/signals/job_boards.py`.**
  - One hiring signal per company per run: the new roles that match the hiring keywords.
  - Reads role title, team, location, date and public URL only. Recruiter names, salaries and descriptions are
    never read.
  - Board names are validated, so no path tricks. A missing or renamed board is skipped; the others still run.
- **`app/adapters/companies_house/officers.py`.**
  - Leadership signals: new directors since the last run. The signal text never carries the person's name.
  - Decision-makers: current directors, natural persons only (no corporate directors or secretaries). We read
    name, occupation, appointment date and country of residence, and nothing else: no date of birth,
    nationality or address. The country is needed because the compliance engine must know the person's
    jurisdiction.
- **`FREE_ONLY` setting** (`true` in both `.env` examples):
  - no paid provider is ever built;
  - the verifier is "none" (every address is `unknown`, never `valid`);
  - live AI goes to the local model, never the paid API;
  - the go-live readiness list no longer asks for Hunter or Anthropic keys, and asks for the free Companies
    House key instead.
- **No address ≠ dead end** (`app/domain/compliance/gate.py`, migration `0015`).
  - A register contact has a name but no email, and none is guessed.
  - When that is the **only** reason the email channel is blocked, the lead becomes a human task,
    `published_contact`: reach the person through the company's own published channels (contact form,
    switchboard, a letter to the registered office), or ask the client for a business address.
  - Any other reason (country, role, client paperwork, suppression) still blocks as before.
  - The compliance engine itself is unchanged.
- The enrichment interface now passes the **company number** from the signal, so register lookups are exact.

## 4. Results
- New tests: 16. They cover:
  - the three job-board formats, filtering, a failing board and rate limiting;
  - officer signals without names, and data minimisation;
  - an exact or ambiguous company match;
  - watchlist validation, and the wiring with and without a key;
  - `FREE_ONLY` never selecting the paid AI.
- End to end on PostgreSQL:
  - an award notice → the director from the register → a prospect with no email → a `published_contact` task;
  - zero paid calls, zero messages sent.
- A director outside the client's buyer personas is still rejected.
- Lint, `mypy --strict` and the 3 import contracts are clean.

## 5. Limits and honest trade-offs
- **No email addresses.** Free-only means every register contact needs a human step, or an address from the
  client or from the prospect's own reply. Volume is lower than with a paid data provider. The legal risk is
  lower too.
- **Directors are not always the buyer.** In small companies (the MSP niche) they usually are. In large ones,
  the persona rules will often reject them, as they should.
- **UK only for decision-makers.** The free EU registers used here confirm legal form but don't list directors.
- **Job boards need the board name.** The client or operator adds it to the watchlist. It is the last part of
  the company's careers-page URL.
- **The local model is slower** (≈ 16 s per reply on the owner's PC) and its accuracy is in `docs/STATUS.md`.
  The live server needs enough CPU/RAM to run it.

## 6. Decisions for you
1. **Solicitor's list:** add "decision-maker names from the Companies House officers register" to the LIA, and
   update the privacy notice's data-source summary (`data_source_summary` in each client's configuration).
2. **Watchlists:** agree with each client which target accounts to watch.
3. **Not free:** hosting, a domain, Microsoft 365 / Google Workspace mailboxes, the solicitor, the accountant and
   the ICO fee. No code changes these.
