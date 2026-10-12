# Phase 1 — Architecture & Plan: Report (2026-10-12)

## 1. Summary
Phase 1 produced the architecture and plan as documents only — no application code, no infrastructure, no external calls. The design centres on one non-bypassable **outbound gateway** (compliance + suppression re-checked at send time, backed by a DB trigger and a CI import contract), a **three-key live switch** keeping everything dry-run by default, **Postgres RLS** for tenant isolation, and a **pure, config-driven compliance engine** where unknown ⇒ block. Stack: FastAPI + Celery/Valkey + PostgreSQL 16/pgvector + Next.js, with a thin Anthropic SDK wrapper. All 33 required tables are specified plus 12 supporting tables. Phase 2–3 backlogs and the full test strategy are defined. Nothing here has been executed or tested yet — these are design documents.

## 2. Changes made
Files created (documents only) in `docs/`: README, 01-architecture, adr/README + ADR-0001…0008, 03-data-model, 04-state-machine, 05-compliance-engine, 06-threat-model, 07-dependencies, 08-repo-layout, 09-backlog-and-test-strategy, PHASE-0-REPORT, PHASE-1-REPORT. No migrations, no dependencies installed.

## 3. Decisions & assumptions (all reversible unless noted)
| Decision | Basis | Reversible |
|---|---|---|
| First niche: UK IT MSPs (demo data) | Default Q1 | Yes |
| Models: `claude-sonnet-5-5` volume, `claude-opus-5-5` reasoning, via config | Default Q2 | Yes |
| Email/enrichment/CRM: mock adapters only; providers chosen before Phase 6 | Defaults Q3, Q5, Q6 | Yes |
| Calendar: Cal.com adapter first | Default Q4 | Yes |
| Celery + Valkey + Postgres outbox (ADR-0001) | Default Q7; RAM/disk | Yes |
| No LangGraph (ADR-0002) | Default Q8 | Yes |
| Local git, folder name kept as `piplineOS` | Default Q9 | Yes |
| Named-rep sender with AI disclosure in footer/on request; approval mode on | Default Q10 | Yes |
| Company details in footer = placeholders | Default Q11 | Yes |
| GB seeded **disabled**; you enable it | Brief: every country starts restricted until you enable it | Yes |
| `risky` email verification ⇒ block | Safest option | Yes |
| At-most-once sending; crashes ⇒ human reconciliation | Duplicate worse than missing | Foundational |
| Global suppression stored as keyed hash under a separate key | Brief + erasure survival | Foundational |

## 4. Requirements traceability (Phase 1 scope)
| Requirement | Status | Evidence |
|---|---|---|
| Architecture & components | Met (design) | 01-architecture |
| Default stack / justified deviations | Met | ADR-0001, 0002, 07-dependencies |
| Tenant isolation design | Met (design) | ADR-0003 |
| Compliance engine: modes, stricter rule, sole traders, IE vs NI, unknown ⇒ block, LIA/DPA gates, queue+send suppression | Met (design) | 05-compliance-engine |
| Gate impossible to bypass | Met (design) | ADR-0004, 01 R1 |
| Dry-run by default | Met (design) | ADR-0004 |
| State machine incl. all side/terminal states | Met (design) | 04-state-machine |
| 33 minimum tables, FK indexes, dedupe/send uniqueness | Met (design) | 03-data-model §3–4 |
| PII encryption, masking, unmask logging | Met (design) | ADR-0005 |
| Prompt versioning, validated LLM output, cost tracking | Met (design) | ADR-0006 |
| Country config without code changes | Met (design) | ADR-0007 |
| Auth, roles, MFA | Met (design) | ADR-0008 |
| Threat model, OWASP, prompt injection | Met (design) | 06-threat-model |
| Lean budget, licences, costs | Partially met | 07 — prices are indicative and must be verified |
| Test strategy, eval datasets, 4 sample traces | Met (plan) | 09 |
| Billing, show-rate, deliverability, EU expansion | Designed at data-model/state level; detailed in their phases | Deferred to Phases 4–7 by plan |
| Dry-run traces for 4 sample leads | Not applicable (no code yet) | First traces at end of Phase 2 |

## 5. Test results
No code exists, so no tests were run. Baseline unchanged: 0 tests, 0 failures.

## 6. Risks & open issues
| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Docker engine not running | High (blocks Phase 2) | Start Docker Desktop; verified at Phase 2 start |
| R4 | Global cross-tenant suppression may make you a controller of that list | High (legal) | Hash-only, contact-prevention purpose; solicitor review |
| R5 | Named-rep sending + AI-written replies | High (legal/trust) | Disclosure config + approval mode; solicitor review |
| R9 | Retention of billing evidence after erasure | Medium (legal) | Anonymised stub; solicitor/accountant review |
| R10 | Indicative provider prices may be wrong | Low | Verify before any purchase |
| R11 | Device link instability during this phase (tool timeouts) | Low | Docs kept in cloud workspace and in git; copied when reachable |

## 7. What you must do
1. **Start Docker Desktop** before Phase 2.
2. Review and approve (or amend) this plan — especially ADR-0001 (Celery vs Temporal) and the sender-identity default (R5).
3. When convenient: your company legal name, number, registered office and privacy URL for the footer (placeholders until then).
4. Later (before Phase 6): solicitor for R4/R5/R9 and footer/LIA/DPA; choose enrichment and sending providers.

## 8. Next phase plan
Phase 2 — Foundations, increments 2.1–2.20 in `09-backlog-and-test-strategy.md`, on branch `phase/2-foundations`, starting with the safety infrastructure (crypto, RLS, audit, config, compliance engine, suppression, state machine, gateway + dry-run) before any agent. **Awaiting your approval.**
