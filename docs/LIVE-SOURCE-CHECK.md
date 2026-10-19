# Live check of the free sources (2026-10-19)

The cloud environment cannot reach most public sources, so until today every free source had only been tested
against recorded examples. This check ran each source once, **live, from the owner's PC**. Everything was
read-only: nothing was stored, sent or paid for, and only counts and company-level samples were printed.

## 1. Results

| Source | Result | Notes |
|---|---|---|
| TED (EU contract awards) | **Works** | 10 awards each for France and Germany; field names as expected |
| Find a Tender (UK awards) | **Works** | 269 award signals in 14 days, most with a Companies House number |
| Greenhouse, Lever, Ashby (job boards) | **Work** | New roles found for test boards |
| France (Sirene), Norway (Brønnøysund), Finland (PRH) registers | **Work** | Correct company and legal form, by number and by exact name |
| Wikidata (company website by number) | **Fixed** | Refused us (403, "respect our robot policy") because the User-Agent named no contact. It now does (`CRAWLER_CONTACT`), and Wikidata answers |
| Contracts Finder | **Returns nothing** | Answers 200 with no records for every query, even a 2024 window. UK notices now go to Find a Tender, which works. Now reported as "silent" (§2) |
| The Gazette (insolvency filter) | **Blocked from this network** | 403 at its CDN, even with a contact User-Agent. The filter already skips it and counts the failure; the register status is still checked. To re-check from the production server |
| Website finder (published business addresses) | **Works, but finds almost nothing** | See §3 |
| Companies House officers and filing stream | **Not checked** | No Companies House key is set on the PC |

## 2. Changes made
- **Contact in the crawler User-Agent** (`CRAWLER_CONTACT`, default the project page). Robots.txt rules still
  match on the `PipelineOS-contact-check` token.
- **"Silent" sources.** A source that answers but finds nothing at all for 7 days (10+ runs) is reported as
  silent, with a metric (`pipelineos_source_silent`), an alert (`SignalSourceSilent`) and a runbook entry. Before,
  only errors were noticed, so Contracts Finder looked healthy.
- **Supplier website from the notice.** When a Find a Tender notice lists the supplier's website, it becomes the
  starting domain (about 1 supplier in 15 lists one). The site must still state the company number to be trusted.
- **A Windows-only test fix.** The import-contracts test failed on the PC when `PYTHONIOENCODING=utf-8` was set,
  because the output was decoded with the Windows code page. It now always decodes UTF-8.

## 3. The contactable rate, measured
The economics model (`GO-TO-MARKET.md` §7–8) assumes that 25% of compliant leads can be emailed in its base case.
For the first time, this was measured on real companies:

- **Sample:** 40 distinct companies that won UK public contracts in the last 14 days (Find a Tender, with a
  company number), run through the free website finder.
- **Result:** **0 of 40** gave a published business address. One had a site that proved it was theirs, but it
  published no generic address. Wikidata knew a website for 1 of the 40.
- **Why:** the bottleneck is finding the website at all. Notices rarely give one, so the finder guesses domains
  from the name (`name.co.uk`, `name.com`...), and those guesses mostly land on parked pages or other companies.
- **Where a site was found,** the proof rule also bites. One real site (it publishes `sales@` on every page) never
  states its company number, which UK companies must show but often don't, so it is not trusted. That is the
  intended safety: a name match alone never makes a site "theirs".
- **Raw supplier data on Find a Tender** (171 supplier entries): 76% have a postcode, 20% a phone number, 6% a
  website, 5% an email address (mostly personal, so not used).

**What it means:** with free sources alone, email reaches almost nobody, so the base case of the economics model
does not hold for the free route. The usable routes are those in `GO-TO-MARKET.md` §7, and choosing between them is
the owner's decision:
1. **The client's own target accounts and contacts** (already built: client self-service, Phase 17). This is now
   the main route.
2. **Published-contact tasks:** a person phones (20% of suppliers list a number) or uses the website's contact
   form. Already built.
3. **A paid data provider** (`FREE_ONLY=false`; Hunter is built behind the paid-call gate). This breaks the "free
   only" rule.

**Optional (owner decision, not built):** also accept a site whose pages show the company's registered-office
postcode together with its exact name. That recovers sites like the one above, but it is weaker proof than the
company number, so it should only follow a solicitor's view on the data-source wording.

## 4. To re-check from the production server (UK)
- The Gazette, which may only block non-UK networks.
- Companies House officers and the filing stream, once the free keys are set.
- `pipelineos signals check …` for each source, as in `PHASE-12-REPORT.md` §5.
