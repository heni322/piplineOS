# ADR-0016: Stage 2 readiness: Norway and Finland, and the EU/EEA representative check

- Status: Proposed (decided under delegated authority, ADR-0011; legal points await counsel)
- Date: 2026-10-14

## Context
Stage 2 of the rollout covers Ireland, the Netherlands and the Nordics. Norway and Finland publish their company
registers as free, keyless open APIs; Ireland's CRO and the Netherlands' KvK charge for access, which is the owner's
spending decision. All Stage 2 countries are configured with `eu_representative_required: true`, and nothing used
that flag. A UK client writing to people in the EU/EEA generally needs an Article 27 representative there.

## Decisions
1. **Registers.** Norway: Brønnøysund Enhetsregisteret (`data.brreg.no`). Finland: PRH YTJ open data v3
   (`avoindata.prh.fi`). Both are read-only, are used only to confirm legal form and status, and never request
   person data (no roles or board endpoints). Each sits behind its own `*_REGISTRY_ADAPTER_MODE`, `mock` by default.
   An unknown form code, an ambiguous name or a missing entry gives `unknown`, which blocks.
2. **Register status counts.** A company that is dissolved, bankrupt or in liquidation per its register is no longer
   treated as a corporate subscriber (`unknown` ⇒ block), in every country. Companies House "open" (a live overseas
   company's UK establishment) counts as active.
3. **EU/EEA representative (engine 1.1.0).** If a relevant country requires a representative and the client is not
   established in the EU/EEA and has none recorded, the engine blocks with `EU_REPRESENTATIVE_MISSING`. The default
   is the safe one (no representative known). Operators record it on the client (audited). The onboarding
   checklist asks for it only when the client targets such a country. A representative never unlocks a country that
   is not enabled.
4. **English first.** Norway and Finland start with English outreach (as their country files say). A client's
   language must have reviewed fixed texts and a footer (`en-GB`, `fr-FR`); other values are rejected rather than
   failing at send time.
5. **Replies in the local language.** Even with English outreach, replies come back in Norwegian, Finnish or Swedish
   (Finland is bilingual). The deterministic safety rules and the injection filter now cover those languages,
   measured on a new 60-reply synthetic set.

## Consequences
- No country is enabled. Norway and Finland stay `restricted_review` until counsel reviews them.
- Ireland and the Netherlands need a paid register decision before their adapters are built.
- Sweden and Denmark registers (Bolagsverket, CVR) are not built yet; Swedish replies are already covered.
