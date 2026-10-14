# Phase 9 — EU rollout Stage 2 readiness: Norway and Finland: Report (2026-10-14)

## 1. Summary
PipelineOS can now prepare outreach to Norwegian and Finnish companies, in English as both country files say. Both
countries stay **disabled**: like every country, they can only be enabled after a lawyer's review is recorded, and
the system enforces that. Ireland and the Netherlands are not built, because their company registers charge for
access, which is a spending decision for the owner.

- **Norwegian and Finnish company registers.** Each company is checked in its own country's official register:
  - Norway: the Brønnøysund Register Centre's open API (Enhetsregisteret, free, no key).
  - Finland: the Finnish Patent and Registration Office's open data (YTJ, free, no key).

  Sole proprietors (ENK in Norway) and partnerships count as individuals and are never emailed. An unknown company
  form, an unknown or ambiguous company, or a former name returns "unknown", which blocks. Organisation numbers and
  business IDs are checked for a valid check digit first. Directors and board members are never requested.
- **Closed companies are no longer treated as companies (all countries).** Before this phase, a company that was
  dissolved, bankrupt or in liquidation in its register still counted as a corporate recipient. It now counts as
  "unknown", which blocks. This was a gap for the UK and France too.
- **EU/EEA representative check (compliance engine 1.1.0).** All Stage 2 countries are marked "EU representative
  required", but nothing used that flag. Under GDPR Article 27, a UK client writing to people in the EU/EEA
  generally needs a representative there.
  - If the client is outside the EU/EEA and has none, every email to such a country is blocked
    (`EU_REPRESENTATIVE_MISSING`).
  - An operator records the representative on the Clients page; this is audited, and the representative must be
    based in the EU/EEA.
  - The onboarding checklist asks for it only when the client targets such a country.
  - A representative never unlocks a country that is not enabled.
- **Replies in Norwegian, Finnish and Swedish.** Even when we write in English, prospects reply in their own
  language (Finland is bilingual, with Finnish and Swedish). A new test set of 60 synthetic replies showed the
  deterministic safety rules were blind to these languages:

  | Rules only (whatever the AI says) | Before | After |
  |---|---|---|
  | Unsubscribes caught | 12% | **100%** |
  | "Stop contacting me" caught | 0% | **100%** |
  | Data-protection requests caught | 38% | **100%** |
  | Hidden instructions escalated | 0 of 8 | **8 of 8** |

  Without the fix, a Norwegian "Avmeld meg" or a Finnish "Poistakaa minut postituslistaltanne" (both mean
  "unsubscribe me") would have relied on the AI alone.
- **Smaller fixes:**
  - A client's language must have reviewed fixed texts and a footer (`en-GB`, `fr-FR`). Previously, a Norwegian
    language setting would only have failed when a message was about to be sent.
  - Nordic consumer email domains (online.no, luukku.com, suomi24.fi, hotmail.se, ...) count as individuals.
  - More Nordic places are recognised (Espoo, Tampere, Trondheim, ...). "Bergen" is deliberately left out
    because towns of that name also exist in the Netherlands and Germany.
  - Oslo, Helsinki and Stockholm time-zone labels.
  - French reply hints, which lifted the offline simulator's French accuracy from 69% to 100%. This is not a
    model result.
  - The privacy notice template has a slot for the representative.

## 2. Changes

| Area | What |
|---|---|
| Registers | `adapters/registries/norway.py`, `adapters/registries/finland.py`; routing now GB, FR, NO, FI; `NO_REGISTRY_ADAPTER_MODE`, `FI_REGISTRY_ADAPTER_MODE` (mock by default) |
| Agent 2 | A non-active register status turns a corporate recipient into "unknown"; Companies House "open" (a live overseas company's UK branch) counts as active |
| Compliance engine | 1.1.0: `CountryRule.eu_representative_required`, `TenantGates.eu_representative` (default: false, the safe value), reason `EU_REPRESENTATIVE_MISSING`; the engine stays pure (import contract kept) |
| Migration `0012` | `tenants.eu_representative` (jsonb, shape-checked). The downgrade refuses while a representative is recorded |
| API / UI | `POST /clients/{id}/eu-representative` (operator only); "Record EU representative" on the Clients page; onboarding checklist item |
| Reply rules | Norwegian, Finnish and Swedish unsubscribe, objection, data-request and out-of-office patterns; interested, not-now, negative and referral hints; 10 injection patterns |
| Evaluation | `tests/evals/datasets/reply_eval_nordic.jsonl` (60 rows, 8 injection attempts); `local_model_run --language nordic` |
| Config | Tenant `language` validated; Nordic free-mail domains, places, time zones |
| Docs | ADR-0016; compliance engine check 15; data model |

## 3. Results

| Check | Result |
|---|---|
| Backend tests (cloud) | **636 passed** (Phase 8: 600) |
| Browser tests | **12 passed** (6 + 6), with accessibility checks |
| Lint, types, 3 import contracts | Clean |
| Dependency audit | No known vulnerabilities; no new dependencies |
| Nordic safety recall, rules only (also with a model that always says "interested") | 100% / 100% / 100%; 8 of 8 injections escalated, 0 acted on |
| English and French results | Unchanged (safety 100%, all injections escalated) |
| Sample-lead traces (`docs/traces-phase-9.json`, fresh database) | Identical decisions to Phase 8 for all 5 leads |
| Nordic replies with Qwen 2.5 7B on the owner's PC | **94.2%** correctly classified; safety 100%; 8 of 8 hidden instructions escalated (§3.1) |
| On the owner's PC | **636 passed, 0 failed, 0 skipped** (§3.2) |

### 3.1 Nordic replies with a real model (Qwen 2.5 7B on the owner's PC, 60 replies, ≈ 14 s each)

| Class | Recall |
|---|---|
| Unsubscribe, objection, data request | 100% |
| Out of office, not now, referral | 100% |
| Interested | 83% (5 of 6) |
| Negative | 75% (3 of 4) |
| Hidden instructions escalated / acted on | 8 of 8 / 0 |

**Overall: 94.2%** correctly classified (French: 90.5%; English: 98.4%). The three mistakes, none of them unsafe:
- "Kiitos, lyhyt puhelu ensi viikolla sopii hyvin." ("a short call next week suits me") was read as *not now*
  instead of *interested*. This loses a meeting but never contacts anyone wrongly.
- "Ei kiitos, tämä ei ole meille ajankohtainen." ("no thanks, not relevant for us") was read as *not now* instead
  of *negative*. The prospect would be re-contacted later rather than closed; the opt-out in every email still
  applies.
- "?" was read as *negative* instead of *unclear*.

### 3.2 On the owner's PC
- Synced as branch `phase/9-nordics-ready` (git bundle; no history rewritten).
- The development and demo databases were migrated to `0012`; nothing was deleted.
- Backend against the PC's Docker PostgreSQL: **636 passed, 0 failed, 0 skipped** (≈ 465 s).
- The local app was restarted on the new code: schema 0012, database, Valkey and web page all OK.

### 3.3 No external side effects
- Every register, provider and model call in the tests goes through an in-process mock transport. No network.
- The new register modes default to `mock`, and the test configuration forces them to `mock`.
- No email, booking, CRM write or paid call is possible: the three-key switch is unchanged and no tenant is live.
- No country was enabled and no checklist item was signed.
- No data, table, file or branch was deleted. The work is on a new branch (`phase/9-nordics-ready`); no history
  was rewritten.

## 4. Before Norway or Finland can be enabled (owner)
1. A Norwegian and a Finnish lawyer review the rules in `config/countries/NO.yaml` and `FI.yaml`. Questions for
   counsel:
   - **Norway** (Marketing Control Act §15): is email to a named employee at a company address treated as email to
     a natural person, which needs prior consent?
   - **Finland** (Information Society Code §200): does B2B email relevant to the recipient's role remain opt-out?
   - **Both:** is an Article 27 representative needed for us as processor as well as for the client?
2. You appoint or confirm the client's EU/EEA representative and record it on the Clients page.
3. You record each review on the Go-live page, then enable the country with your authenticator code.
4. Clients sign the LIA and DPA covering these countries, as for UK clients.

## 5. Next
- Sweden and Denmark registers (Bolagsverket and CVR). Swedish replies are already covered.
- Ireland (CRO) and the Netherlands (KvK) once you decide on paid register access.
- Stage 4 (ES, IT, BE, PT): register, texts, footer and test set, following the France pattern.
