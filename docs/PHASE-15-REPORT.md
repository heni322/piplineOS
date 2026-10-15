# Phase 15 — Source health: Report (2026-10-14)

## 1. Why
The free sources call many outside services: contract notices, TED, job boards, the Gazette, Wikidata and the
Companies House stream. Five of them could not be checked against the live services from the cloud. If one changes
its format, removes a board or times out, two things went wrong before this phase:
- **One broken source stopped the whole hunt.** Only rate limits were caught, so, for example, TED rejecting a field
  name would also have stopped that client's UK contract awards.
- **One broken client stopped every client's scheduled hunt.**
- **Nobody would know.** A failing source simply found nothing, and the only trace was a log line.

## 2. What changed
- **Each source is isolated.** A source that errors is skipped for that run; the others carry on. One client's
  failure no longer stops the other clients' hunts.
- **Every run is recorded** in `source_runs` (migration 0018), for each source in each client's hunt and for the
  filing-stream ingest:
  - when it ran and how many items came back;
  - the outcome: ok, partial (for example one job board missing), rate limited or failed;
  - on failure, only the error type and HTTP status. Never a response body, a company list or anything about a
    person.
- **The Go-live page has a "Source health" table.** For each source:
  - healthy, degraded or failing;
  - runs, failures and items found in the last 24 hours;
  - last run, last success and last error.
- **Monitoring.**
  - New metrics: `pipelineos_source_failing{source}` and `pipelineos_source_failures_24h{source}`.
  - New alert `SignalSourceFailing`, raised after 30 minutes. It says where to look and which check command to run.

**Status rules:**

| Status | When |
|---|---|
| Failing | the last run failed, and either it has never succeeded or it failed 3 or more times in 24 hours |
| Degraded | it failed, ran partially or was rate limited recently, but still succeeds |
| Healthy | otherwise |

## 3. Results
- Backend: **749 passed, 0 failed** (Phase 14: 745). 4 new tests:
  - a broken, a rate-limited and a partial source in one hunt;
  - the scheduled hunt survives a broken client;
  - the three status levels;
  - the operator API (sign-in required) and the metrics.
- Frontend: lint, types and unit tests pass. **All 14 browser tests pass.** The governance browser test now also checks the Source health table and
  runs the WCAG checks with it on the page.
