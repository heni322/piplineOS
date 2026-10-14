# Phase 10 — Stage 4 readiness (Spain, Italy, Portugal, Belgium) and the Irish register: Report (2026-10-14)

## 1. Summary
PipelineOS can now handle prospects in Spanish, Italian, Portuguese and Dutch, and can confirm Belgian and Irish
companies from their official registers for free. Every one of these countries stays **disabled** until a lawyer's
review is recorded. Spain, Italy, Portugal and the Netherlands also stay blocked for email until you choose a paid
register (§4).

- **Replies in Spanish, Italian, Portuguese and Dutch: a gap found and closed.** The safety rules' own
  description said they covered Spanish, Italian and Dutch. A new test set of 61 synthetic replies showed otherwise:

  | Rules only (whatever the AI says) | Before | After |
  |---|---|---|
  | Unsubscribes caught | 25% | **100%** |
  | "Stop contacting me" caught | 25% | **100%** |
  | Data-protection requests caught | 50% | **100%** |
  | Hidden instructions escalated | 3 of 8 (2 acted on by a careless model) | **8 of 8**, none acted on |

  Examples that slipped through before: "No deseo recibir más correos suyos", "Non voglio più ricevere le vostre
  email", "Não quero receber mais emails vossos", "Ik wil geen e-mails meer van u ontvangen".
- **Everything a prospect sees, in their language**, with formal address: "usted", "Lei", third person in
  Portuguese, "u" in Dutch.
  - Slot offers, confirmations, reminders and dates ("jueves 15 de octubre a las 15:00 (hora de Madrid)").
  - The unsubscribe and reschedule pages, chosen by browser language.
  - A legal footer per language (provisional, for counsel).
  - French-speaking Belgium uses the French texts from Phase 8.
- **Writing checks in each language.** A draft is blocked if it:
  - uses informal address;
  - contains spam words ("gratis", "senza impegno", "sem compromisso", "vrijblijvend", ...);
  - pushes for a meeting;
  - mentions sensitive personal topics;
  - writes its own legal or unsubscribe text.

  "TI", Spanish and Portuguese for IT, is deliberately not treated as the informal "ti".
- **Belgian and Irish registers, free, from open-data files.** Neither country offers a free lookup service, but
  both publish the whole register as a file:
  - Belgium: KBO/BCE open data, free after registration.
  - Ireland: CRO "Company Records", CC BY 4.0, daily.

  You import the file once (`pipelineos registers import be|ie <file>`), and lookups then read the local copy with no
  network call. Rules for the copy:
  - **Companies only.** A Belgian sole trader's entry, which is a person's name, is never stored. The import
    refuses to run if it cannot tell people from companies.
  - **No addresses or other details.** Only number, official names, legal form and status are kept.
  - **Legal forms come from the file's own descriptions**, not guessed code numbers; unrecognised forms are
    "unknown".
  - **Re-imports never delete.** A company that disappears from the file becomes "not in extract", which blocks.
  - The application can read the copy but not change it; only your import command writes it.
- **Ireland, corrected.** I had said Ireland's register needs paid access. It doesn't: the CRO publishes its
  register for free, so Ireland is now covered. The Netherlands' free dataset leaves out company names and
  numbers, so it cannot confirm a company; that one stays your decision.
- **Smaller additions:** Spanish, Italian, Portuguese and Belgian places (Sevilla, Milano, Lisboa, Gent, ...);
  consumer mail (libero.it, sapo.pt, telefonica.net, skynet.be, ...); time-zone names.

## 2. Changes

| Area | What |
|---|---|
| Reply rules | Spanish, Italian, Portuguese and Dutch unsubscribe, objection, data-request (and erasure/access type), out-of-office, referral, interest/not-now/negative patterns; Portuguese language detection; 4 more injection patterns |
| Evaluation | `tests/evals/datasets/reply_eval_stage4.jsonl` (61 rows, 8 injection attempts); CI test; `local_model_run --language stage4` |
| Texts | `core/i18n.py`: es-ES, it-IT, pt-PT, nl-BE catalogues (no missing keys, tested), weekdays, months, time zones; 24-hour "15:00" outside French; language mapping by prefix (nl-NL → nl-BE, es-MX → es-ES) |
| Footers | `config/footers/{es-ES,it-IT,pt-PT,nl-BE}.yaml` (provisional, for counsel) |
| Writer checks | Formal address per language, spam words, high-pressure call-to-action, sensitive topics, model-written legal text |
| Registers | `adapters/registries/local_copy.py` (shared store and lookup), `belgium.py` (KBO import), `ireland.py` (CRO import); `BE_` / `IE_REGISTRY_ADAPTER_MODE` (mock by default); CLI `pipelineos registers import be\|ie` |
| Migration `0013` | `register_companies`, `register_names` (reference data; the application has read-only access; the downgrade refuses while data exists) |
| Docs | ADR-0018; compliance engine §4; data model; go-live runbook A.8 |

## 3. Results

| Check | Result |
|---|---|
| Backend tests (cloud) | **693 passed** (Phase 9.1: 663) |
| Lint, types, 3 import contracts | Clean |
| Stage 4 set, rules only, also with an always-"interested" model | Unsubscribe, objection, data request: 100%; 8 of 8 injections escalated, 0 acted on |
| English, French and Nordic sets | Unchanged (100% safety) |
| Stage 4 replies with Qwen 2.5 7B on the owner's PC | **98.1%** correctly classified; safety 100%; 8 of 8 injections escalated (§3.2) |
| On the owner's PC | **693 passed, 0 failed, 0 skipped** (§3.3) |

### 3.2 Spanish, Italian, Portuguese and Dutch replies with a real model (Qwen 2.5 7B on the owner's PC, 61 replies, ≈ 16 s each)
- **98.1%** correctly classified. This is the best of all sets: English 98.4%, Nordic 95.4%, French 90.5%.
- Every class was at 100%, including interested, negative and not now.
- The single miss: "?" was read as *negative* instead of *unclear*.
- Hidden instructions: 8 of 8 escalated, 0 acted on.

### 3.3 On the owner's PC
- Synced on branch `phase/9-nordics-ready` (git bundle; no history rewritten).
- The development and demo databases were migrated to `0013` and the country files re-synced (Sweden SEK,
  Denmark DKK); nothing was deleted.
- Backend against the PC's Docker PostgreSQL: **693 passed, 0 failed, 0 skipped** (≈ 480 s).
- The local app was restarted on the new code: schema 0013, database, Valkey and web page OK.

### 3.4 No external side effects
- The register imports in the tests use synthetic files built in a temporary folder.
- No register was downloaded and no network call was made.
- No country was enabled and nothing was signed.
- No data, table, file or branch was deleted. A draft migration for a Belgium-only table, created and replaced
  within this phase, was never committed or applied outside the test database.

## 4. Decisions for you
1. **Spain, Italy, Portugal and the Netherlands have no free register that confirms legal form.** Their companies
   stay "unknown" and are never emailed, even after a lawyer's review. Options, all paid:
   - per-lookup extracts from each official register: Registro Mercantil (ES), Registro Imprese / InfoCamere (IT),
     Certidão Permanente (PT), KVK API (NL);
   - a single commercial aggregator covering all four.

   I have not chosen or contacted any supplier. This is a spending decision.
2. **Belgium and Ireland**, when you want them:
   - download the files (Belgium after a free registration with FPS Economy);
   - run the import;
   - add the CC BY attribution for Ireland to the privacy notice.
3. **For each country:** a lawyer's review of the country rules and the footer, a native speaker's review of the
   fixed texts, then record the review and enable the country on the Go-live page.
