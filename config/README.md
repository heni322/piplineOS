# Configuration as versioned data (ADR-0007)

These YAML files are the reviewed source of truth for legal, tax and governance rules.
`pipelineos config sync --path config` validates them and writes **new immutable versions** to the
database whenever a file's content changes. Nothing is ever updated or deleted in place.

Rules:
- `verified_by_counsel` / `verified_by_accountant` must be `false` here. Only an operator can attest
  verification, in the dashboard, which records who and when. A YAML file claiming `true` is rejected.
- Countries are seeded **disabled**. Enabling a country is an operator action (audited), never a file edit.
- Unknown country, missing rule or ambiguous data => the engine blocks and flags for review.
- Adding a country = adding a file here. No code change.

NOT LEGAL OR TAX ADVICE. Every value below needs review by a UK solicitor / accountant before go-live.
