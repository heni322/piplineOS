# Phase 14 — Client targeting form: Report (2026-10-14)

## 1. Why
Phases 11 and 12 added free sources that each client steers with its own settings:
- **industry keywords**: contract awards in the UK and the EU;
- **hiring keywords and a watchlist**: job boards and new directors;
- **SIC codes**: market-wide funding filings;
- **target countries**: EU awards.

These could only be set by sending a complete configuration as raw JSON to the API, so in practice nobody would.

## 2. What changed
- **API (operator only).** `GET` and `PUT /api/v1/clients/{id}/targeting` read and change only the targeting
  part of a client's configuration:
  - Each save is a new configuration version, through the same validation as before.
  - Qualification criteria and pricing are untouched, so the terms the client signed **stay signed**. A test checks
    this.
  - Keywords are trimmed and de-duplicated, and blank lines are dropped.
  - Countries must have country rules.
  - Errors say what is wrong, for example "SIC codes are 5 digits" or a job-board name in the wrong form.
- **Clients page: a "Targeting" panel** with:
  - four keyword boxes, one entry per line, each with a short explanation of what it drives;
  - country checkboxes, with a reminder that each country still needs its Go-live sign-off;
  - a watchlist editor: company, UK company number, job board and website; add and remove rows.
- **Browser tests.** Parallel tests competed for single-use sign-in codes. A shared lock now lets one sign-in run
  at a time, each on a fresh code, and a stale lock from a crashed run expires.

## 3. Results
- Backend: 3 new API tests:
  - a round trip, with signed terms unchanged;
  - six kinds of invalid input refused with a reason;
  - client users and anonymous callers refused.
- Frontend: lint, types, unit tests and build pass. **All 14 browser tests pass**, full stack in the cloud,
  including the new targeting test with WCAG 2.1 AA checks.
