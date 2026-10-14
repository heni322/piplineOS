# ADR-0018: Stage 4 languages, and registers published as open-data files

- Status: Proposed (decided under delegated authority, ADR-0011; legal and wording points await counsel and native speakers)
- Date: 2026-10-14

## Context
Stage 4 covers Spain, Italy, Portugal and Belgium. Two questions decide what can be built without spending money:
1. Can prospects there be handled in their own language, safely?
2. Can their companies be confirmed in an official register?

Registers:
- **Belgium** (KBO/BCE) and **Ireland** (CRO) publish their whole register as free open-data files: Belgium after a free registration, Ireland under CC BY 4.0. Neither offers a free lookup service.
- **Spain, Italy and Portugal** charge per extract or by subscription. The **Netherlands'** free dataset omits company names and numbers, so it cannot confirm a company.

## Decisions
1. **Local copies for file-based registers.**
   - The owner imports the file (`pipelineos registers import be|ie`); lookups read the copy, with no network call.
   - One shared store (`register_companies`, `register_names`, migration 0013) holds legal persons only, with no addresses.
   - Re-imports never delete; companies that leave the extract become `not_in_extract`, which blocks.
2. **Legal forms come from the files' own descriptions.**
   - Belgium's `code.csv` and Ireland's type text are matched by keyword, not hard-coded numbers.
   - The import refuses to run if it cannot tell legal persons from natural persons.
3. **No register, no email.** Spain, Italy, Portugal and the Netherlands get no register adapter until the owner chooses a paid source. Their companies stay `unknown` and are blocked by the existing engine rule.
4. **Languages.**
   - Fixed texts, dates, public pages and footers in Spanish, Italian, European Portuguese and Belgian Dutch, with formal address: "usted", "Lei", third person in Portuguese, "u". French-speaking Belgium uses the French texts.
   - The writing checks block informal address, spam words, pushy meeting requests, sensitive topics and model-written legal text in each language.
   - All of it is provisional until native-speaker review.
5. **Reply safety measured, not assumed.**
   - The rules already claimed to cover Spanish, Italian and Dutch. A new 61-reply test set showed they caught 25% of unsubscribes and objections, and 50% of data requests; 3 of 8 injections were escalated.
   - The set now runs in CI, with the requirement that a model which ignores safety cannot lower recall.

## Consequences
- Belgium and Ireland can be confirmed for free once the owner imports the files.
- Spain, Italy, Portugal and the Netherlands remain blocked for email until a paid-register decision, even after counsel review.
