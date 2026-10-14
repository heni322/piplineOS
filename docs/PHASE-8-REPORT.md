# Phase 8 — EU rollout Stage 3 readiness: France: Report (2026-10-14)

## 1. Summary
PipelineOS can now work for a French client in French. France stays **disabled**: like every country, it can only be enabled after a solicitor's (avocat's) review is recorded, which the system enforces. The French wording is marked as provisional until a native speaker or counsel reviews it.

- **French company register.** The government's free *API Recherche d'entreprises* (Sirene data, no key) confirms a French company's legal form by SIREN number or exact name.
  - Sole traders (entrepreneurs individuels) and groupings without legal personality count as individuals, so they are never emailed.
  - Ambiguous, restricted ("non-diffusible") or unknown records return "unknown", which blocks.
  - Directors' names are never read.
- **Country routing.** Each company is checked in its own country's register: UK → Companies House, France → Sirene. A country without a register returns "unknown", which blocks.
- **Everything a French prospect sees is in French**, with the formal "vous":
  - slot offers, booking confirmations, reminders and reply subjects
  - dates ("jeudi 15 octobre à 15h00 (heure de Paris)")
  - the unsubscribe and reschedule pages (by browser language)
  - the legal footer (`config/footers/fr-FR.yaml`)

  The English wording is unchanged.
- **French writing checks.** Drafts are blocked if they:
  - use the informal "tu"
  - write their own legal or unsubscribe text
  - contain French spam words ("gratuit", "sans engagement", ...) or sensitive personal topics
  - push for a meeting ("réserver un rendez-vous")
- **Stronger French safety rules (found by the new test set).** Before this phase, the deterministic rules caught only 70% of French unsubscribes and 60% of French objections. Phrasings like "Je ne souhaite plus recevoir vos e-mails", "merci de ne plus m'envoyer…" and "ne plus me contacter" slipped through. They now catch **100%**, whatever the AI says. French hidden-instruction detection grew from one pattern to nine.
- **French test set.** 50 labelled synthetic French replies, including 8 hidden-instruction attempts, checked in CI.

## 2. Changes

| Area | What |
|---|---|
| Registers | `adapters/registries/france.py` (INSEE legal-form mapping, SIREN or exact name), `adapters/registries/routing.py`; registry lookups take the company's country; `FR_REGISTRY_ADAPTER_MODE` (mock by default) |
| Texts | `core/i18n.py`: en-GB + fr-FR catalogue (no missing keys, tested), French dates and time zones, browser language for public pages |
| Messages | Slot offers, confirmations, reminders and reply subjects follow the client's language |
| Public pages | Unsubscribe and reschedule pages in French or English, `<html lang>` set, escaped once |
| Footer | `config/footers/fr-FR.yaml` (provisional, for counsel) |
| Writer checks | Formal address, French spam words, legal-text and sensitive-topic patterns, high-pressure call-to-action patterns |
| Reply rules | French unsubscribe, objection and out-of-office patterns; 8 more French injection patterns |
| Evaluation | `tests/evals/datasets/reply_eval_fr.jsonl`; `local_model_run --language fr` |

## 3. Results

| Check | Result |
|---|---|
| Backend tests | **600 passed** (Phase 7: 580) |
| French safety recall, rules only (also with a model that always says "interested") | unsubscribe 100%, objection 100%, data requests 100% |
| French hidden instructions escalated / acted on | 8 of 8 / 0 |
| English results | unchanged (100% safety, all injections escalated) |
| French replies with Qwen 2.5 7B (local, free) | **90.5%** correctly classified; safety 100%; 8 of 8 hidden instructions escalated (§3.1) |
| Sample-lead traces (`docs/traces-phase-8.json`, fresh database) | Identical decisions to Phase 6.3 |
| On the owner's PC | **600 passed, 0 failed, 0 skipped** (§3.2) |

### 3.1 French replies with a real model (Qwen 2.5 7B on the owner's PC, 50 replies, ≈ 15 s each)

| Class | Recall |
|---|---|
| Unsubscribe, objection, data request | 100% |
| Out of office, not now, referral | 100% |
| Interested | 60% (3 of 5) |
| Negative | 67% (2 of 3) |

The four mistakes, none of them unsafe:
- "Bonjour, volontiers pour un échange la semaine prochaine." was read as *not now* instead of *interested*.
- "Oui, envoyez-moi le document, je le transmettrai à l'équipe." was read as *referral* instead of *interested*.
- "Non merci, nous avons déjà un prestataire." was read as *objection* instead of *negative*. This errs toward caution and stops contact.
- "?" was read as *negative* instead of *unclear*.

English with the same model: 98.4%. French is usable for testing; a stronger model is recommended for live French replies, as for English emails.

### 3.2 On the owner's PC
- Synced as branch `phase/8-france-ready` (git bundle, includes Phase 7; no history rewritten).
- The development and demo databases were migrated to `0011`; nothing was deleted.
- Backend against the PC's Docker PostgreSQL: **600 passed, 0 failed, 0 skipped** (≈ 559 s).
- The local app was restarted on the new code (schema 0011, using Qwen 2.5 7B).

## 4. Before France can be enabled (owner)
1. An avocat reviews:
   - the French rules in `config/countries/FR.yaml` (CNIL, B2B opt-out)
   - the French footer, privacy notice and texts
   - the need for an EU representative (`eu_representative_required: true`)
2. A native French speaker reviews the fixed texts in `backend/app/core/i18n.py`.
3. You record the review on the Go-live page, then enable France (authenticator code).
4. A French client signs a French-language LIA and DPA, as for UK clients.

## 5. Next (Stage 2 and 4 countries)
- Stage 2 (IE, NL, Nordics): register adapters. Norway (Brønnøysund) and Finland (PRH) offer free open APIs; Ireland's CRO and the Netherlands' KvK need paid access, which is a decision for you.
- Stage 4 (ES, IT, BE, PT): the same pattern as France: register, texts, footer, test set.
