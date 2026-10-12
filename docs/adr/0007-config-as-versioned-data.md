# ADR-0007 — Legal, tax and country rules as versioned data

**Status:** Proposed

## Decision
- Source of truth: YAML files in `config/` (reviewed via git PRs): `countries/*.yaml`, `tax_rules.yaml`, `transfers.yaml`, `footers/*.yaml`, `holidays_overrides.yaml`, `llm_prices.yaml`.
- `pipelineos config sync` validates YAML against Pydantic schemas and inserts **new immutable versions** into DB tables (`compliance_rules`, `tax_rules`, `country_rollout_status`, …) when content hash changes. Never updates or deletes a version row.
- Each row carries `version`, `content_hash`, `source`, `verified_by_counsel` / `verified_by_accountant` (always `false` when written by code), `last_reviewed`, `effective_from`.
- Only an operator action in the dashboard can set `verified_by_counsel = true`, which creates a new version recording who attested and when. Code never sets it.
- Adding a country = adding a YAML file + operator enabling it. No code change (requirement).
- Bank holidays come from the `holidays` library (England & Wales `GB-ENG`, Scotland `GB-SCT`, Northern Ireland `GB-NIR`, Ireland `IE`, etc.) with YAML overrides for one-off additions.

## Reversibility
High.
