# Phase 6.3 — The client's CRM (HubSpot): Report (2026-10-13)

## 1. Summary
The architecture had a CRM connection from day one, but only as an empty interface and a mock. It is now built, with HubSpot first (chosen under the CEO delegation, ADR-0015). Everything stays in dry-run.

- **The client connects their own HubSpot** from a new "Your CRM" page in the client portal.
  - They paste a private-app access token. It is stored encrypted, is never shown again, and never travels by email or chat.
  - They choose their pipeline and which deal stage each meeting status should use. HubSpot's standard pipeline is pre-filled.
  - Disconnecting needs a written reason. Nothing is ever deleted.
- **Each meeting PipelineOS books appears in the client's CRM:**
  - the company (matched by web domain, else created)
  - the contact (matched by email)
  - a deal in their pipeline, linked to both
  - the deal's stage follows the meeting: booked, confirmed, attended, no-show, cancelled
- **Attendance from the CRM counts as evidence.** When the client moves a deal to their "attended" stage after the meeting, that is recorded as attendance from the CRM. Billing already accepts it; the operator's word alone never makes a meeting billable.
- **Same safety rules as email.**
  - Nothing is written until all three live keys are on. Until then each would-be write is recorded, encrypted, in the dry-run log.
  - "Test" mode also stays dry-run, because the connected CRM is always the client's real one.
- **Objections and erasure apply here too.** A person who objected, was suppressed or erased is never sent; only an existing deal's stage may change.
- **No duplicates after a crash, bounded retries.** The deal name carries our reference, so a retry finds the deal instead of creating a second one. Failures are retried at most 5 times, and the stored error is only a status code, never the provider's text.
- **Automatic:** every 5 minutes for every active client with a connected CRM. The operator can also trigger a sync for one client.
- The demo client is connected to a fictional HubSpot, so the portal page and the dry-run log show it working.

## 2. Changes

| Area | What |
|---|---|
| Migration `0010` | `crm_connections` (one per client; encrypted token; who may write: the client's admin or the operator) and `crm_links` (CRM object ids only; written by the system only). No DELETE anywhere. The downgrade refuses while either table holds data |
| Adapter | `adapters/crm/base.py` (records and interface), `adapters/crm/hubspot.py`, an in-memory mock |
| Gateway | `domain/outbound/crm.py`: connect, disconnect, status, `sync_tenant` (three-key decision, dry-run record, live write, attendance read-back, retries) |
| Attendance | `record_attendance` stores evidence (CRM deal id and stage) |
| API | `GET/PUT /crm/connection`, `POST /crm/connection/disconnect` (client admin or operator; a client is always pinned to their own organisation), `POST /crm/sync/run` (operator). New permission `manage_integrations` |
| Worker | `pipelineos.system.crm_sync` every 5 minutes |
| Portal | `/client/crm` "Your CRM" page and navigation link |
| Demo | Demo client connected to a fictional HubSpot; `run_conversations(..., attend=False)` for tests |
| Tests | 5 contract (HubSpot), 6 security / integration (CRM sync), 1 browser test (portal page with accessibility checks); the readiness test now reads the latest migration instead of a fixed number |
| Docs | ADR-0015, data model, this report |

Branch `phase/6.3-crm` (from `phase/6.2-live-data`).

## 3. Requirements traceability

| Requirement | Status | Evidence |
|---|---|---|
| No CRM writes unless adapter, client and go-live are all live | Met | `test_crm_writes_wait_in_the_dry_run_log_until_all_three_keys_are_on` (key 1 only, and sandbox: 0 calls), `test_live_write_and_attendance_read_back_only_with_all_three_keys` |
| Dry-run records the full would-be effect | Met | Encrypted payload with deal, stage, company and contact; one record per change, not per run |
| Suppression and erasure apply on every path | Met | `test_a_person_who_objected_is_never_sent_to_the_crm` (live: nothing sent; dry-run: no email in the record) |
| Tenant isolation | Met | `test_another_client_cannot_see_or_change_the_connection` (other client: 0 rows; viewer: read-only; client can't write sync records; nothing can be deleted) |
| Secrets never exposed or committed | Met | Token write-only (API and page), encrypted at rest, not in the audit log (`test_client_connects_their_own_crm…`, browser test); secret scan clean |
| Attendance evidence from calendar or CRM, not operator alone | Met | CRM read-back records source `crm` with deal id and stage |
| Idempotency and bounded retries | Met | Contract test: a retry finds the deal by reference; `test_failures_are_recorded_without_provider_text_and_retried_a_bounded_number_of_times` |
| Migrations reversible, no data deletion | Met | 0010 up → down → up on an empty database; down refused when data exists |

## 4. Test results

| Suite | Result |
|---|---|
| Backend | **564 passed** (6.2: 553): unit 292, contract 73, integration 109, security 84, evals 5, property 1. One failure on the first run was the readiness test's fixed schema number; it now reads the latest migration |
| Frontend components | 12 passed |
| Browser (2 stages, fresh databases) | **12 passed** (6 + 6), accessibility checks included (new page: "Your CRM", view and connect form) |
| Lint / types / import contracts | Clean; 3 contracts kept (the HubSpot adapter is only reachable through the outbound layer) |
| Sample-lead traces (`docs/traces-phase-6.3.json`, fresh database) | Identical decisions to 6.2 |
| Secret scan | No leaks (tree) |
| On your PC | See §4.1 |

### 4.1 On your PC
_Filled in after the sync._

**No external side effects:** every HubSpot call in the tests goes to an in-process fake or the in-memory mock; no HubSpot token exists anywhere except the demo's fictional one; all demo CRM updates are in the dry-run log.

## 5. Defects found while building (fixed, with tests)

| # | Defect | Fix |
|---|---|---|
| D39 | The sync compared a meeting's change time (database clock) with the run's own clock, so a change could be missed | The sync stores the meeting's own change stamp |
| D40 | The retry counter reset at the start of each attempt, so the 5-attempt cap never triggered | Counter kept across attempts |
| D41 | The readiness test had the schema number hard-coded | Reads the latest migration |

## 6. Risks and open issues

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R39 | Clients on other CRMs (Pipedrive, Salesforce, Microsoft Dynamics) | Medium | Same interface; build on demand |
| R40 | HubSpot's API moves to dated versions (`/crm/objects/2026-09/…`); we use the stable v3 paths | Low | Paths are in one adapter; switch when HubSpot announces v3 retirement |
| R41 | A client that renames or deletes a pipeline stage | Low | Writes fail and are retried at most 5 times, then shown as "need attention" on their page |
| R42 | Personal data in the client's CRM is the client's responsibility as controller; erasure there is theirs | Medium (legal) | On the solicitor's list with the DPA |

## 7. What you must do
Nothing now.
