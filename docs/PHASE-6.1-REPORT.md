# Phase 6.1 — Completing the dashboard and Google Workspace: Report (2026-10-13)

## 1. Summary
This closes the gaps left after Phase 6 that needed no decision from the owner. Everything stays in dry-run.
- **Go-live and governance page:**
  - the three live keys at a glance
  - the go-live checklist with sign-off (authenticator code + evidence link)
  - countries: record the solicitor's review, then enable (authenticator code); disable with a reason
  - tax rules: record the accountant's review
  - UK–EU adequacy expiry
- **Leads page:** masked by default; showing a person's details asks for a reason and is logged; each lead's compliance decisions.
- **Inboxes page:** today's sends against the cap, 7-day live bounce and complaint rates against the limits, why an inbox was paused; manual pause and resume, each with a written reason.
- **Overview:** run the pipeline and send approved emails (dry-run) from the dashboard.
- **Google Workspace**, behind the same three-key switch as Microsoft 365:
  - Gmail sending, as raw MIME with the unsubscribe headers, reading back the Message-ID Gmail actually used
  - Calendar free/busy and meetings (a retry can't create a second meeting)
  - replies by Gmail push, with Google's signed token verified before anything is queued
  - each inbox says which provider it uses; the live sender routes on that
- **Two security fixes** (D32, D33) found while building this.

## 2. Changes

| Area | What |
|---|---|
| API | `GET /governance/status`, `GET /countries`, `POST /countries/{code}/counsel-verification` (MFA), `/enable` (MFA, needs the review), `/disable` (reason); inbox health in `GET /inboxes`, `POST /inboxes/{id}/pause`; `GET /campaigns`; `POST /webhooks/google` |
| Migrations | `0008` config tables writable only by operator/system; single-use login codes. `0009` Gmail history cursor on inboxes; inbox provider must be `mock`, `msgraph` or `google` |
| Adapters | `google_auth.py` (service-account JWT, push-token verifier; no new dependency), `email/gmail.py`, `calendar/google.py`, `inbound/gmail.py` |
| Outbound | `providers.py`: `RoutingEmailSender` by inbox provider; `calendar_gateway(..., provider)`; system mail provider setting |
| Workers | `gmail_history_task`, `renew_gmail_watches_task` (daily) |
| Dashboard | `/operator/governance`, `/operator/leads`, `/operator/inboxes`, overview run controls, navigation |
| Tests | `test_governance_admin.py` (9), `test_google_adapters.py` (23), `test_google_switch.py` (4), `governance.spec.ts` (2 browser tests with accessibility checks) |
| Config | `GOOGLE_*` and `SYSTEM_MAIL_PROVIDER` in the env examples; Google option in the go-live runbook |

Commits on `phase/6.1-completion` (from `phase/6-production-readiness`): 15e5d00, c4aaf19, and the docs commit.

## 3. Defects found (fixed, with tests)

| # | Defect | Impact if shipped | Fix |
|---|---|---|---|
| D32 | The legal, tax and supplier configuration tables, and the invoice number counter, had no row-level security. No endpoint allowed it, but at database level a client session could add a rule version (for example one marked as reviewed by a solicitor) or burn an invoice number | A future bug could have let a client weaken a compliance rule or break gapless invoice numbering | Writes allowed only for the operator or the system (config sync); everyone can still read. `test_config_tables_refuse_writes_from_a_client_context` |
| D33 | A login authenticator code could be reused within its 90-second window | Someone who saw your password and a code could sign in again with the same code | Each code works once at login. Step-up codes for sign-offs are unchanged, so signing off several items doesn't make you wait. `test_a_login_code_works_only_once` |
| D34 | Screen-reader-only table labels widened the page on phones | Sideways scrolling on mobile | Contained within the table's scroll area; all mobile screenshots now 390 px wide |

## 4. Test results

| Suite | Result |
|---|---|
| Backend | **531 passed**, 0 failed (Phase 6: 495) |
| Frontend components | 12 passed |
| Browser (2 stages, fresh databases) | **11 passed** (6 + 5), accessibility: 0 violations, including the three new pages |
| Lint / types / import contracts | Clean; 2 contracts kept (Gmail and Calendar adapters are only reachable through the outbound gateway) |
| Migrations | head (0009) → 0007 → head → 0008 → head: OK |
| Dependency audits | Backend and frontend production: no known vulnerabilities |
| Sample-lead traces | Decisions identical to Phase 6 (UK corporate → dry-run sent; sole trader and German prospect → alternative channel; suppressed → blocked; suppressed after approval → blocked at send) |
| **Total** | **554** (Phase 6: 516) |

**No external side effects:** every Google and Microsoft call in the tests goes to an in-process fake; no credentials are configured; the demo still records sends in the dry-run log only.

## 5. What the owner decides (unchanged)
Solicitor review, ICO fee, accountant review, the go-live sign-offs, the enrichment and email-verification providers, and the USD→GBP rate for AI costs. The new Go-live page is where the reviews and sign-offs are recorded, by the owner, with their authenticator code.
