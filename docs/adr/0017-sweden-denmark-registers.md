# ADR-0017: Sweden and Denmark company registers

- Status: Proposed (decided under delegated authority, ADR-0011)
- Date: 2026-10-14

## Context
Sweden's and Denmark's official registers are free, but unlike Norway, Finland and France, both need credentials issued on registration:
- Bolagsverket's high-value-datasets API uses an OAuth client.
- Erhvervsstyrelsen's CVR system-to-system access uses a user ID and password.

The owner applies for these (no spend). Until then, the adapters run only against recorded examples.

## Decisions
1. **Sweden.**
   - Look up by organisation number only, because the API has no name search. A company without a known number stays "unknown", which blocks.
   - A personal identity number (a sole trader's) is never sent. The number is recognised before any call and returns "unknown".
   - The legal form comes from Statistics Sweden's "juridisk form" code. Deregistration and pending liquidation or restructuring mark the company as not active.
2. **Denmark.**
   - Search by CVR number or exact current name, asking only for four fields (number, name, form code, status). Participants (owners, board) are never requested.
   - Credentials are only ever sent over HTTPS; the adapter refuses a plain-HTTP address.
   - Partnership companies (P/S) are treated cautiously, like UK limited partnerships, until counsel says otherwise.
3. **Unverified field names.** Field names follow each register's published schema. Before a country is enabled, a sandbox lookup of one known company must confirm them (go-live runbook, A.8).
4. **Currencies.** The country files had Sweden and Denmark in EUR; they are corrected to SEK and DKK.
