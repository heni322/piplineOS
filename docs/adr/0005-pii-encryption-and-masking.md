# ADR-0005 — PII encryption, blind indexes and masking by default

**Status:** Proposed

## Context
PII must be encrypted at rest and in transit, access logged, and masked by default (notably for operators in Tunisia). We still need exact-match lookups (dedupe, suppression) on emails.

## Decision
- **Envelope encryption at the application layer** for PII columns (`*_enc bytea`): AES-256-GCM via the `cryptography` library, per-tenant data keys wrapped by a master key (`PII_MASTER_KEY` locally; cloud KMS in production). Key ID stored with each ciphertext to support rotation.
- **Blind indexes** for lookup: `email_bidx = HMAC-SHA256(BLIND_INDEX_KEY, normalise(email))`. Normalisation: trim, lowercase, Unicode NFKC. (No Gmail-style dot/plus stripping for corporate domains — documented choice.)
- **Suppression hashes** use a *separate* global key (`SUPPRESSION_HASH_KEY`) so suppression survives tenant key deletion (crypto-shredding) and erasure.
- **Erasure = crypto-shredding + row anonymisation**: PII columns nulled, prospect row kept as an anonymised stub for aggregate metrics and billing evidence (with legal review flag), suppression hash retained.
- **Masking**: API serialisers return masked values (`J*** S***`, `j***@a***.co.uk`) unless the request carries an unmask grant for that lead; each unmask writes `pii_access_log` (user, lead, reason, IP country, timestamp). Default for every role is masked.
- **In transit**: TLS everywhere in staging/prod; Postgres `sslmode=require` outside local Docker network.
- **Disk**: provider-level encryption on volumes and backups in addition to column encryption.
- **Logs**: structured logger has a PII scrubber; emails and names never logged in clear (blind index or masked only). Tested.

## Alternatives
- `pgcrypto` in-DB encryption: keys would transit to the DB in queries/logs. Rejected.
- Full-disk only: does not protect against app-role or backup leaks. Insufficient alone.

## Consequences
− No SQL `LIKE` search on names/emails; search uses blind indexes (exact) or a masked, tokenised company-name index (companies are not personal data).

## Reversibility
Medium — changing schemes requires a re-encryption migration; key rotation is designed in.
