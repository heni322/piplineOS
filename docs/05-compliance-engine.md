# 05 — Compliance Engine Specification

Status: **Proposed (Phase 1)**

> Not legal advice. Every rule ships with `verified_by_counsel: false`. Unknown, unverified or ambiguous ⇒ **block and flag for human review**.

## 1. Interface

```python
def evaluate(ctx: ComplianceContext) -> ComplianceDecision: ...
```

A **pure function** of its input (no I/O). The caller loads the context in one transaction and persists the decision. Pure ⇒ exhaustively unit-testable and deterministic.

`ComplianceContext`: tenant gates (LIA approved?, DPA signed?, tenant status, role controller/processor), campaign channel (`email`), prospect (country, location country, verified email status, subscriber type + reason), company (country, legal form), rule set snapshot (rule per country, with version), rollout status per country, suppression result (global / tenant hit, checked_at), stage (`queue` | `send` | `referral` | `recheck`).

`ComplianceDecision`: `outcome` (`allow` | `block` | `alternative_channel`), `reason_codes[]`, `effective_country`, `rules_applied{country: rule_id@version}`, `allowed_channels[]`, `engine_version`.

## 2. Evaluation order (first failing step wins; all reasons still collected)

| # | Check | Fails → | Reason code |
|---|---|---|---|
| 1 | Suppression (global or tenant) | `block` + lead → `suppressed` | `SUPPRESSED_GLOBAL` / `SUPPRESSED_TENANT` |
| 2 | Tenant LIA reference + approval present | `block` | `TENANT_NO_LIA` |
| 3 | Tenant DPA signed (processor tenants) — operator's own tenant needs LIA only | `block` | `TENANT_NO_DPA` |
| 4 | Tenant active, campaign active, not auto-paused | `block` | `TENANT_PAUSED` / `CAMPAIGN_PAUSED` |
| 5 | Countries known: prospect location country **and** company country both present and valid ISO-3166 (with UK subdivisions) | `block` | `COUNTRY_UNKNOWN` |
| 6 | Each relevant country has an **enabled** rollout status | `block` | `COUNTRY_NOT_ENABLED` |
| 7 | Each relevant country has a current rule | `block` | `RULE_MISSING` |
| 8 | **Stricter rule** across {prospect location, company country} (see §3) | per mode | — |
| 9 | Mode `restricted_review` | `block` | `COUNTRY_RESTRICTED_REVIEW` |
| 10 | Mode `consent_required` (no consent record for this channel) | `alternative_channel` | `CONSENT_REQUIRED` |
| 11 | Subscriber type: `individual` ⇒ treat as consent_required; `unknown` ⇒ block | `alternative_channel` / `block` | `INDIVIDUAL_SUBSCRIBER` / `SUBSCRIBER_TYPE_UNKNOWN` |
| 12 | Email verified `valid` (`risky` ⇒ block by default, tenant cannot override) | `block` | `EMAIL_NOT_VERIFIED` |
| 13 | Role relevance: prospect title matches tenant persona (computed by Agent 2, ≥ threshold) | `block` | `ROLE_NOT_RELEVANT` |
| 14 | Channel in rule's `allowed_channels` | `block` | `CHANNEL_NOT_ALLOWED` |
| 15 | A relevant country's rollout config says `eu_representative_required` and the client is neither established in the EU/EEA nor has an EU/EEA representative recorded (GDPR Article 27; engine 1.1.0) | `block` | `EU_REPRESENTATIVE_MISSING` |
| — | All passed | `allow` | `OK` |

**Strictness order**: `restricted_review` > `consent_required` > `permitted_with_opt_out`. Missing anything ⇒ strictest.

## 3. Jurisdiction resolution

- Countries are ISO-3166-1 alpha-2 with UK nations carried as subdivisions (`GB-ENG`, `GB-WLS`, `GB-SCT`, `GB-NIR`) for holidays; **compliance rules attach to `GB` as a whole** and to `IE` separately.
- **Ireland (`IE`, EU, EU GDPR + Irish ePrivacy rules) ≠ Northern Ireland (`GB-NIR`, UK GDPR + PECR).** Parsing tests include "Belfast", "Derry/Londonderry", "Dublin", "Cork", "NI", "N. Ireland", "Republic of Ireland", "Eire", ".ie domain with Belfast address".
- Domain TLD is **never** used alone to infer jurisdiction (a `.co.uk` company may have a prospect in Germany). It may raise `COUNTRY_CONFLICT` for human review.
- Regime for DP requests: `uk_gdpr` if prospect located in UK, `eu_gdpr` if in EU/EEA; if company and prospect span both, record both and apply the stricter deadline handling (both are one month by default; config).

## 4. Subscriber-type classification (Agent 2 + deterministic rules)

| Evidence | Result |
|---|---|
| Companies House match: `ltd`, `plc`, `llp`, public body, charity company | `corporate` |
| Legal form `sole_trader`, `partnership` (non-LLP), `lp` (where individual partners) | `individual` |
| No registry match, or registry ambiguous | `unknown` ⇒ **block** |
| Generic free-mail domain (gmail.com, outlook.com, …) | `individual` ⇒ never emailed |

Companies House API is free and official (adapter with mock; sandbox via real API key in Phase 6). Each company is checked in its own country's register: France (Sirene, Phase 8), Norway (Brønnøysund Enhetsregisteret) and Finland (PRH YTJ) (Phase 9), all free and keyless. A country without a register returns `unknown` ⇒ block. A register entry that is dissolved, bankrupt or in liquidation also gives `unknown` (Phase 9).

## 5. Seed configuration (all `verified_by_counsel: false`)

Every country — including GB — is seeded **disabled** in `country_rollout_status` and only the operator can enable it. Proposed seed modes (what the rule says *once enabled*):

```yaml
# config/countries/GB.yaml
country_code: GB
name: United Kingdom
regime: uk_gdpr
mode: permitted_with_opt_out          # corporate subscribers only
allowed_channels: [email, alternative]
subscriber_rules:
  corporate: permitted_with_opt_out
  individual: consent_required        # sole traders, non-LLP partnerships
  unknown: restricted_review
required_message_elements:
  - sender_name
  - company_legal_name
  - company_registration_number
  - registered_office_address
  - article_14_notice_with_privacy_link
  - one_click_unsubscribe
notes: >
  PECR reg. 22 (as amended by DUAA 2025): B2B email to corporate subscribers,
  role-relevant, sender identified, valid opt-out in every message.
  Legitimate interest still requires a documented LIA and an easy right to object.
source: "ICO guidance on PECR & direct marketing (to be cited precisely by counsel)"
verified_by_counsel: false
last_reviewed: null
rollout_stage: 1
holiday_calendars: [GB-ENG, GB-SCT, GB-NIR]   # Wales shares the England & Wales calendar
time_zones: [Europe/London]
languages: [en-GB]
currency: GBP
```

| Country | Proposed mode once enabled | Stage | Notes |
|---|---|---|---|
| GB | permitted_with_opt_out (corporate only) | 1 | Seeded disabled; operator enables after LIA + counsel review |
| IE | restricted_review | 2 | Irish ePrivacy rules for B2B email need counsel review |
| NL, SE, DK, FI, NO | restricted_review | 2 | Each needs local review |
| FR | restricted_review | 3 | B2B opt-out regime commonly cited; must be verified (CNIL) |
| ES, IT, BE, PT | restricted_review | 4 | |
| DE, AT | consent_required | Restricted | Email blocked; alternative channels only |
| *any other / unknown* | restricted_review | — | Default; also applied when no file exists |

## 6. Gate points (defence in depth)

1. **Queue time** — after enrichment, before Agent 3 spends LLM tokens.
2. **Pre-approval** — re-evaluated when a reviewer opens the item (stale banner if rule version changed).
3. **Send time** — inside the outbound gateway transaction (authoritative).
4. **Referral** — referred person gets a fresh evaluation.
5. **Rule change** — when a rule version changes, all leads in `compliance_checked`…`approved` are re-evaluated by a job; newly blocked leads move out of the queue.

## 7. Mandatory footer (template, UK)

```
—
{sender_name}, {sender_title} · {company_legal_name}
Registered in England & Wales No. {company_number} · {registered_office_address}

We found your professional details via {data_source_summary} and are contacting you
about {purpose} because it relates to your role. You can object at any time — reply
"unsubscribe" or use this link: {one_click_unsubscribe_url}. Privacy notice and your
rights: {privacy_url}
```

- For client campaigns the footer names the **client** (controller); PipelineOS (processor) is listed in the client's privacy notice as a processor, not in the footer.
- Agent 3 never writes the footer; it is rendered deterministically and appended after generation. A post-render validator checks every required element is present ⇒ otherwise the message cannot leave `draft`.
- Wording is a placeholder pending solicitor review (go-live checklist).

## 8. Global suppression — open legal point

Suppressing an identity across all tenants means PipelineOS uses tenant A's data for tenant B's campaign. Proposed safeguard: store only a keyed hash, use it solely to *prevent* contact, and record PipelineOS's own controller purpose and lawful basis for the list in the operator privacy notice. **Flagged for solicitor review** (Phase 0 risk R4); the behaviour (global suppression) follows the brief.
