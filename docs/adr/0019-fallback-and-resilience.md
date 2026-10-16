# ADR-0019: Fallback and resilience: an outage defers work, never decides it

- Status: Proposed (decided under delegated authority, ADR-0011)
- Date: 2026-10-16

## Context
PipelineOS depends on many services it does not control:
- the AI model (local Ollama when free-only, or the API);
- Microsoft Graph and Gmail (sending, inbound, calendar);
- Companies House and the other registers, the Gazette, procurement and job-board feeds, DNS, HubSpot;
- inside, Valkey (broker and limiter) and PostgreSQL.

A dependency-by-dependency audit (what happens when each one is slow, down, rate-limited or returns garbage) found that safe paths existed (dry-run, the reconciler, the inbound sweep, the paid gate) but that several failures did real harm:

| # | Failure | Consequence before |
|---|---|---|
| 1 | Gmail accepted the email, then the Message-ID read-back failed | Marked "retry": **the same email sent twice** |
| 2 | Any 5xx on a send | Treated as "not sent" and retried: possible duplicate |
| 3 | Model unreachable (connection refused, 5xx) | Raw exception: the reply was not classified, so **even an unsubscribe waited** |
| 4 | Model unreachable during drafting | Lead escalated to a person (an outage is not a person's job) |
| 5 | Celery `autoretry_for=(ConnectionError,)` | Matched none of httpx, SQLAlchemy, kombu or LLM errors: **retries never fired** |
| 6 | One lead / message / reply / tenant failing | Stopped every item after it (no isolation) |
| 7 | Provider refusing sends | Retried every 5 minutes forever, no backoff, no cap |
| 8 | Poison outbox event | Retried every 5 seconds forever |
| 9 | 5.7.x bounce (policy: our reputation or authentication) | Treated as a dead address: **the person suppressed everywhere** |
| 10 | DNS resolver timeout | MX "missing"; daily check cleared the sending domain's verification |
| 11 | Hunter "still checking" (202) | Rejected by compliance for good |
| 12 | Gmail inbox with more than 100 new messages | Cursor jumped past the rest: **replies skipped** |
| 13 | Valkey down | Every API request waited for a socket timeout |
| 14 | Google key endpoint briefly down | Every genuine push notification rejected |
| 15 | No database timeouts; no task time limits | A hung dependency held workers; a slow task was re-delivered and ran twice |

## Decision
**One rule: an outage defers work; it never decides it.**
- No lead is rejected, escalated or lost because something we depend on was unreachable.
- No email is sent twice.
- Safety (unsubscribe, objection) never waits for the model.

### 1. Failure taxonomy, decided by error type only (`app/adapters/http.py`)
| Type | Meaning | What callers do |
|---|---|---|
| `RetryableProviderError` (incl. `RateLimited`, `ProviderUnreachable`, `LLMUnavailable`) | The provider certainly did nothing: 429, 503, connection refused, DNS failure, connect timeout, no access token; or any network failure on a **read** | Defer and retry later |
| `ProviderError` | A definite refusal (4xx) | Do not retry |
| `AmbiguousSendError`, read timeout on a **write** | The request may have been carried out | Never retried automatically; a human reconciles |

Every adapter's HTTP client classifies transport errors this way. `ProviderUnreachable` is also an `httpx.TransportError`, so existing `except httpx.HTTPError` code is unchanged.

### 2. At-most-once sending
- The sending session (access token) is opened **before** anything is handed over; a failure there is retryable (`open_before_send`).
- After the provider accepts the email, nothing may raise. The Gmail Message-ID read-back falls back to our own deterministic Message-ID.
- `check_send`: only 429 and 503 mean "refused, nothing done"; any other 5xx is ambiguous and goes to reconciliation.

### 3. Bounded retries with backoff
- **Sends:**
  - `messages.send_attempts` and `next_attempt_at` (migration 0025);
  - exponential backoff (2, 4, 8 … minutes, capped at 60), never sooner than the provider's Retry-After;
  - after 6 attempts: `failed_permanent` plus a `send_failed` escalation;
  - a definite 4xx refusal fails at once.
- **Outbox:**
  - backoff, capped at 2 hours;
  - after 10 attempts the event is dead-lettered (left in place, `pipelineos_outbox_dead`, alert);
  - events waiting out a backoff are not selected, so healthy events are never stuck behind a poison one.
- **Celery:**
  - `autoretry_for=TRANSIENT_ERRORS` (`app/core/resilience.py`), with jittered exponential backoff;
  - every job is idempotent (state-checked), so a retry never repeats a side effect.

### 4. Circuit breakers (`CircuitBreaker`: closed → open for a cooldown → one trial call)
- **Model:** one breaker per backend type in the process (3 failures, then 120 s). During an outage the first few calls fail; the rest fail fast with `LLMUnavailable`.
- **Campaign run:** after 3 consecutive leads fail on an unreachable provider, the run stops. The remaining leads keep their state for the next run, instead of calling the provider once per lead.
- **Send loop:** when a provider refuses an inbox's message, that inbox's other messages wait until the next tick.
- **Valkey rate limiter:** after 2 failures, the per-process bucket answers alone for 30 s. Limits get looser, but never disappear and never add a timeout per request.

### 5. Bulkheads (one item never stops the others)
- Per lead in `run_campaign`.
- Per message in `send_due`: an unknown outcome is left in `sending` for reconciliation, and the loop goes on.
- Per reply in `process_inbound`: the first failure is re-raised after the others are done, so push tasks still retry.
- Per tenant in every system job: send, CRM sync, reconciliation, retention, reply SLA, kill switch, inbound fetch.

### 6. Model outage fallback
- `LLMUnavailable` is an `LLMEscalation` (and a `RetryableProviderError`), so:
  - **Reply classification:** the deterministic safety rules decide first, and an unsubscribe is honoured with no model at all. Anything else goes to a person, as before.
  - **Writer:** the lead stays `compliance_checked`; the next campaign run drafts it, and nobody is asked to handle an outage.
  - **Trigger scoring:** the signal is skipped and seen again within the lookback window.
  - **Qualification and reply drafting:** a person decides. A missing model never produces "criteria not met".
- **Rejected alternative: falling back to another model.** In free-only mode the only other "model" is the simulator, which writes fake text. Using it on live work would be worse than waiting.

### 7. Transient states are not answers
- DNS: a resolver timeout raises (the lead waits) instead of meaning "no MX". The daily sending-domain check uses strict lookups, so an outage keeps yesterday's result.
- Hunter "still checking" (202): the lead waits.
- Bounces are classed by RFC 3463 code:
  - **5.1.x / 5.2.1:** the address is dead, so it is suppressed.
  - **Other 5.x.x (policy):** the bounce counts against the sending inbox (the kill switch can pause it) and that lead's pending follow-ups stop. The person is **not** suppressed.
  - **4.x.x:** temporary; logged only.

### 8. Stale-if-error and timeouts
- Google push keys: if the refresh fails, keys up to 24 h old are still used. After that, verification fails closed.
- Database:
  - connect timeout 5 s;
  - statement timeout 120 s;
  - lock timeout 15 s.
  These raise `OperationalError`, which jobs treat as transient.
- Celery: soft/hard limits of 25/30 minutes (hunt: 50/55), below the broker's 60-minute redelivery, so a job never runs twice in parallel.
- The Gmail inbox cursor only advances past history records whose messages were all returned.
- The Companies House filing-stream ingest saves its cursor just before an unreachable-register lookup (resumes there next run) and skips a company the register refuses outright, so one company can never stall the stream.
- Job boards: one board's server error is reported as partial; the others are still read.

### 9. Visibility
- New gauges:
  - `pipelineos_outbox_dead`
  - `pipelineos_sends_retrying`
  - `pipelineos_sends_failed_24h`
- New alerts:
  - `OutboxDeadLetter`
  - `SendsFailing`
  - `ProviderRefusingSends`
- Breakers log `circuit_opened` and `circuit_closed`.

## Consequences
- An outage of any one dependency now causes delay, not wrong decisions, duplicates or lost replies. Every behaviour above has a test (`tests/unit/test_resilience.py`, `tests/integration/test_resilience*.py`, contract tests for Gmail and Graph).
- Breakers are per process: each worker learns about an outage on its own (a few failed calls per worker). That is acceptable at this size; a shared breaker in Valkey would add a dependency to the failure path.
- **Still open, documented:**
  1. Reply handling runs the model and calendar calls inside one database transaction. A failure rolls the whole reply back; it is retried, with no wrong outcome, but locks are held for the call's duration. The fix is to restructure so external I/O happens outside the transaction.
  2. A meeting reschedule cancels the old event before creating the new one; a failure in between needs a person.
  3. Website contact lookup cannot tell "site down" from "no site", so a company whose site is down at that moment gets a human task instead of an email address.
  4. The insolvency check skips a source that fails (logged). The register status is checked again before contact.
  5. Inbox and CRM OAuth expiry is not yet detected automatically. Sends now fail and escalate after the attempt limit rather than looping.
