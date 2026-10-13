# Phase 6.2 — Real data sources for Agents 1 and 2: Report (2026-10-13)

## 1. Summary
Agents 1 and 2 could only work on fictional demo data. They now have real sources, chosen under the CEO delegation (ADR-0011). Everything stays in dry-run.

- **Agent 1 — buying signals from public contract awards.**
  - Contracts Finder and Find a Tender, the UK government's two procurement feeds. Both are free, public and need no key.
  - When a company wins a public contract matching the client's ICP keywords, that becomes a signal.
  - The supplier's Companies House number is taken from the notice when it is given, so the registry check is exact instead of a name search.
  - Contact names, emails and phone numbers printed in the notices are never read.
- **Agent 2 — finding the decision-maker and checking their email.**
  - Hunter.io, an EU-based provider that builds work emails from public web pages (no LinkedIn scraping).
  - Searches by company name when the signal had no website, and returns the company's domain with the person.
  - Gets the person's country and region, which the compliance rules need.
  - Verifies the address.
  - Never stores social-profile links or phone numbers.
- **Paid lookups are gated like sending.** A Hunter lookup costs credits, so it follows the brief's rule for paid API calls: only with all three keys on.
  - Until then, the would-be lookup is written (encrypted) to the dry-run log.
  - The lead waits as "discovered". It is never thrown away for want of a lookup.
  - "Test" mode uses Hunter's free test key (dummy data).
- **Objections at the source are respected.** If Hunter says a person asked for their data not to be processed, that address is suppressed everywhere and nothing about them is stored.
- **No double work.** A company is matched by Companies House number or name when there is no website. A company that turns out to share a domain with one the client already has is closed as a duplicate.
- **Automatic hunting.** Every 6 hours (configurable, 0 = off), each active campaign looks for new signals and processes waiting leads, including ones whose lookup was postponed.
- **Go-live page:** a new "Where the data comes from" list shows, for each source, whether it uses demo, test or live data.

## 2. Changes

| Area | What |
|---|---|
| Signals | `adapters/signals/ocds.py`: one parser for both feeds. Award notices only; follows the publisher's next-page links on the same site only; ICP keywords to include and `-word` to exclude. `RawSignal.company_number` added |
| Agent 1 | Companies without a website are matched by Companies House number, then name; the number is stored |
| Registry | Companies House lookup by number (exact; 404 → unknown) |
| Hunter | `adapters/hunter.py`: domain search by domain or company name (max 10 results = 1 credit), location lookup for at most 3 people, verifier. Key sent in a header, never in the URL. 403 → rate limited; 429 → credits used up (not retried); 451 → objection |
| Paid-call gate | `domain/paid/gate.py`, `domain/paid/providers.py`: three-key decision per client; dry-run record with the lead id; checked before any credit is spent |
| Agent 2 | Defers instead of disqualifying; adopts the resolved domain; duplicate-domain check; 451 → global suppression |
| Erasure | Data-request erasure also clears the new dry-run records tied to the lead |
| Import contract | New `paid-only`: only the paid-call gate can build the Hunter adapter (3 contracts kept) |
| Scheduling | `hunt_active_campaigns` + Celery `pipelineos.pipeline.hunt`; `HUNT_INTERVAL_HOURS`, `HUNT_LOOKBACK_DAYS`; workers pass the client context to providers |
| Dashboard | "Where the data comes from" on the go-live page; "Run pipeline" uses the configured sources |
| Settings | `HUNTER_API_KEY`, `HUNT_INTERVAL_HOURS`, `HUNT_LOOKBACK_DAYS` in both env examples |

Branch `phase/6.2-live-data` (from `phase/6.1-completion`).

## 3. Decisions (under delegation, ADR-0011)

| Decision | Why | Reversible |
|---|---|---|
| Public contract awards as the first real signal source | Free, official, legal to use, no scraping, and a strong growth trigger for the demo niche. They carry company numbers for an exact registry match | Yes (more sources plug into the same interface) |
| Hunter.io for both finding and verifying | One EU-based supplier and one data-processing agreement instead of two. Data comes from public web pages, not LinkedIn. Documented test key, rate limits and objection handling | Yes (adapter behind an interface) |
| Paid lookups need all three keys | The brief: no paid API calls unless adapter, client and go-live are all live | — |
| Defer, don't disqualify, in dry-run | A lead that hasn't been looked up hasn't failed; it is picked up again on the next run | — |
| Take location for at most 3 candidates per company | Location decides which country's rules apply; capping it caps the cost (0.2 credits each, charged only when found) | Yes |
| Never store social-profile links or phone numbers | Data minimisation; not needed to send an email | — |

## 4. Requirements traceability

| Requirement | Status | Evidence |
|---|---|---|
| Agent 1 finds real buying signals | Met (contract awards) | `test_contracts_finder_award_notices_become_tender_signals`, `test_find_a_tender_uses_its_own_path_and_notice_links` |
| Web content is untrusted data; no LinkedIn scraping | Met | Notice text goes through the existing injection sanitiser; notice contact details never read (asserted); Hunter's LinkedIn field dropped (asserted) |
| Agent 2 uses licensed data, with a source per field | Met | `prospect_field_sources` shows `hunter.io domain-search …` and `hunter.verifier` (`test_sandbox_uses_the_free_test_key…`) |
| No paid API call unless all three keys are on | Met | `test_paid_lookups_wait_in_the_dry_run_log_until_all_three_keys_are_on` (0 requests), `test_real_lookups_happen_only_with_all_three_keys` |
| Unknown → block, not guess | Met | No location → unknown country (blocked by the engine); ambiguous registry → unknown; 202 "still checking" → unknown |
| Objection respected everywhere | Met | `test_a_person_who_objected_at_the_source_is_suppressed_and_never_contacted` |
| Never contact the same organisation twice | Met | Company-number/name matching; `test_a_company_resolving_to_a_known_domain_is_not_worked_twice` |
| Erasure covers every place personal data lands | Met | Erasure query extended to the new dry-run records |
| Scheduled operation | Met | `test_scheduled_hunt_runs_every_active_campaign` |

## 5. Test results

| Suite | Result |
|---|---|
| Backend | **553 passed**, 0 failed (6.1: 531). Breakdown: <ul><li>unit 292</li><li>contract 68</li><li>integration 109</li><li>security 78</li><li>evals 5</li><li>property 1</li></ul> |
| New tests | 15 contract (feeds, Hunter, Companies House by number), 7 security / integration (paid gate) |
| Frontend components | 12 passed |
| Browser (2 stages, fresh databases) | **11 passed** (6 + 5), accessibility checks included. The go-live test now also checks the new data-sources list |
| Lint / types / import contracts | Clean; **3 contracts kept** |
| Dependencies | None added (the HTTP client was already in use) |
| Sample-lead traces (`docs/traces-phase-6.2.json`, fresh database) | Identical decisions to 6.1. UK corporate → dry-run sent; sole trader and German prospect → alternative channel; suppressed → blocked; suppressed after approval → blocked at send |
| On your PC | See §5.1 |

### 5.1 On your PC
_Filled in after the sync._

**No external side effects:**
- Every test request to Hunter, Contracts Finder, Find a Tender and Companies House goes to an in-process fake.
- No Hunter key exists anywhere.
- In the demo, all sources stay on demo data.

To confirm the real shape of the two government feeds, I made two read-only requests to their public APIs from your PC. I looked only at field names, stored nothing, and used no personal data. The test fixtures are synthetic.

## 6. Defects found

| # | Defect | Impact | Fix |
|---|---|---|---|
| D35 | A signal without a website created a new company record every time | Duplicate companies; the same supplier could be worked twice | Match by Companies House number, then name |
| D36 | Agent 2 would have marked a lead "no matching contact" when the lookup simply hadn't happened | Good leads lost in dry-run | Deferred instead |
| D37 | The Companies House record's number could be wiped when the registry returned nothing | Lost data | `coalesce` keeps the known number |
| D38 | The secret scanner flagged a synthetic token in a Phase 6 browser test | CI's full-history scan would fail | That exact synthetic value allow-listed (history untouched); tree and history scans clean |

## 7. Risks and open issues

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R36 | Hunter's coverage of small UK firms is unmeasured | Medium | Measure on real runs in test mode with your key; the adapter can be swapped |
| R37 | Award notices are a narrow trigger set (public-sector suppliers) | Low | Job-posting and funding sources can plug into the same interface |
| R38 | Hunter is a new sub-processor | Medium (legal) | Added to `config/transfers.yaml` as **proposed**; its data-processing agreement and data location go on the solicitor's list |

## 8. What you must do
Nothing now.

## 9. Next
- Job-posting signals (a second free source).
- Measure Hunter's hit rate in test mode.
- Hunter's data-processing agreement on the solicitor's list (it is recorded as a proposed sub-processor).
