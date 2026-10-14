# Phase 12 — A stronger free search: Report (2026-10-14)

## 1. Why
Phase 11 made the system free, but left three gaps:
- triggers came mostly from a watchlist the client had to write;
- register contacts had **no email address**, so every lead needed a human step;
- the EU countries had no trigger source at all.

This phase closes all three with free, official, lawful sources.

## 2. What the system now searches

| Agent | Source | Cost | New in |
|---|---|---|---|
| 1 Triggers | UK contract awards (Contracts Finder, Find a Tender) | free | Phase 6 |
| 1 Triggers | **EU contract awards (TED)** for the client's EU countries | free, no key; attribution | **12** |
| 1 Triggers | **Funding filings for the whole UK market**: new shares (SH01) and new charges (MR01), by the client's SIC codes, from the Companies House stream | free streaming key | **12** |
| 1 Triggers | Watchlist: job boards (Greenhouse, Lever, Ashby) and new directors | free | 11 |
| 1 Filter | **Insolvency**: Gazette corporate-insolvency notices and register status, checked *before* any AI is spent | free, Open Government Licence | **12** |
| 2 Decision-maker | Directors from the Companies House register | free key | 11 |
| 2 Email | **The company's own published business address**, taken only from a site proven to be theirs | free | **12** |
| AI | Local model (`FREE_ONLY`) | free | 11 |

## 3. How each piece works, and its safeguards

### 3.1 Funding signals for the whole market (`register_ingest.py`, `stream.py`, migration 0016)
- **The ingest job.** Every 5 minutes it reads one bounded slice of the Companies House filing stream.
  - It keeps only SH01 and MR01 filings.
  - It looks up each company's name, status and SIC codes, and stores them once for all clients.
- **Companies only.** Forms that can name shareholders are never downloaded. No personal data is stored.
- **Exactly once.** The saved position moves only past events fully handled:
  - a rate limit or the per-run lookup cap stops early, and the next run resumes there;
  - a replay stores nothing twice.
  - If the job was off longer than the stream keeps history, it restarts from now and logs the gap.
- **Per client:** add the target SIC codes to the configuration, e.g. `"sic_codes": ["62020", "62090"]`. Active
  companies with those codes that filed in the hunt window become "funding" triggers. No watchlist is needed.

### 3.2 Insolvency filter (`insolvency.py`)
- **The Gazette first.** It shows winding-up petitions before the register's status changes. The search is by
  company number, in the corporate-insolvency category only. A hit needs the **exact** number as a whole word.
- **The register second.** Any status other than active means the company is skipped.
- **When a source fails,** it is skipped and logged. Agent 2 checks the register status again anyway.
- **Safe XML.** The Gazette's XML is parsed with `defusedxml`; a test proves a hostile document is refused.

### 3.3 Published business address (`website_contacts.py`)
1. **The website.** In order: the domain we already hold, Wikidata's official website for the company number
   (CC0), or up to six names derived from the company name.
2. **Proof it's theirs.** UK companies must show their registered number on their website. A site is accepted
   only if one of its pages states the number; a name alone is never enough.
3. **The address.**
   - Only generic role addresses on that domain, in this order: sales, enquiries, hello, contact, info, office…
   - **Never** personal addresses (first.last@), nor privacy, careers, accounts or no-reply addresses.
4. **Checked.** The domain must have a mail server. Only an address found this way is reported "valid".
   Everything else stays "unknown", and compliance blocks it.
5. **Polite and safe.**
   - robots.txt is respected; a robots.txt that refuses us means we stay away.
   - At most 7 small pages per site.
   - Redirects only within the same domain.
   - **Only public internet addresses are contacted.** Private, loopback and link-local addresses are refused
     (SSRF protection).
- **The result.** The email is addressed to the named director at the company's published address. Compliance
  decides as usual, and a human approves every draft. When no verified site is found, the lead becomes the
  Phase 11 "published contact" task, as before.

### 3.4 EU contract awards (`ted.py`)
- TED's search API: award notices since the last run, for the client's EU countries. The UK is not on TED.
- **Matching.** Notice titles are matched locally against the client's keywords, including exclusions.
- **Fields.** Multilingual fields are read in English first. Contact points are never requested.

### 3.5 Live check command
`pipelineos signals check <ted|job-board|gazette|website|officers|stream>` calls one source once, live:
- it stores nothing;
- it prints counts and company-level samples only, never people's names.

## 4. Results
- New tests: **25**:
  - stream parsing, bounded slices, resume and restart;
  - the ingest's exactly-once behaviour on PostgreSQL;
  - per-client SIC filtering and the insolvency skip;
  - Gazette matching and safe XML;
  - website ownership proof, generic-only addresses, robots.txt, redirects, private hosts, Wikidata and MX;
  - TED request shape, languages, paging and countries;
  - end to end: register → published address → compliance "allow" → drafted, nothing sent.
- Full backend suite: **737 passed, 0 failed** in the cloud (Phase 11: 715).
- Self-review fixes:
  - website pages are now streamed with a hard 512 KB cap;
  - the email pattern is bounded. Unbounded, a hostile page could make the scan take hours.
- Lint, `mypy --strict` and the 3 import contracts are clean.

## 5. Limits — read before going live
1. **Formats not yet checked against the live services.** This cloud environment cannot reach TED, the Gazette,
   Wikidata, the job boards or the stream. The adapters follow the published documentation, and the tests use
   that shape. On your PC, run once each, and tell me if anything comes back empty or errors:
   - `uv run pipelineos signals check ted --target FR`
   - `uv run pipelineos signals check gazette --target <a company number>`
   - `uv run pipelineos signals check website --target <number> --name "<company name>"`
   - `uv run pipelineos signals check job-board --target greenhouse:<board>`
   - `uv run pipelineos signals check stream` (needs `COMPANIES_HOUSE_STREAM_KEY`)

   TED's field names are the most likely to need a small fix.
2. **Generic addresses reach a shared inbox,** not the director directly. Reply rates will be lower than with a
   personal address. That is the trade-off for staying free and lawful.
3. **SIC codes are self-declared by companies,** so some funding triggers will be off-target. The AI relevance
   score (threshold 60) filters them out.
4. **TED covers only contracts above EU thresholds.**

## 6. Decisions for you
1. **Solicitor's list:** add to the LIA and the privacy notice's data-source summary:
   - "company website (published business contact addresses)";
   - "Companies House filings";
   - "TED".
2. **Per client:** set `sic_codes`, and the EU countries in `icp.countries` when wanted.
3. **Create a free Companies House streaming key** (developer hub → new application → streaming key) and set
   `COMPANIES_HOUSE_STREAM_KEY`.
