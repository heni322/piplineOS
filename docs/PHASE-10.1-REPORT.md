# Phase 10.1 — Replies safety net: Report (2026-10-14)

## 1. Why
Real mailboxes (Microsoft 365, Google Workspace) deliver replies by **push**. Pushes can be lost:
- our endpoint was down during a deploy;
- a subscription lapsed;
- Gmail's change history became too old to read (the code then skipped ahead).

Nothing went back to look. A lost reply might be an **unsubscribe or an objection**, and missing it would mean
emailing someone who asked us to stop. A review of the inbound code also found two smaller problems:
- **The reply-polling job crashed when real mailboxes were on.** Every minute it raised "inbound mailbox
  adapters are not implemented", even though real replies arrive by push.
- **An unsubscribe from an unknown sender could be handled twice if the push was delivered twice.** Microsoft
  does sometimes deliver a notification twice. This opened two data-protection requests. Only replies from known
  prospects were de-duplicated.

## 2. What changed
- **A 15-minute safety sweep** (`app/workflows/inbound_sweep.py`, Celery `pipelineos.replies.sweep_inbound`).
  - For every real mailbox of an active client, it lists the last 72 hours of inbox messages: IDs and the
    Message-ID header only, no bodies.
  - It skips anything already handled or sent by us, fetches only the rest, and runs the normal reply handling
    on them.
  - A failing mailbox doesn't stop the others. Mock mailboxes are skipped.
- **Handled exactly once.** Unknown-sender messages are now recorded as handled by a keyed hash of their
  Message-ID (`inbound_seen`, migration 0014; nothing readable is stored). A duplicate push, the sweep and a late
  push all count as one message.
- **An alert when it matters.** Each recovery is audited (count only). A new metric
  (`pipelineos_replies_recovered_24h`) and alert (`RepliesRecoveredBySweep`) tell you the push path is broken
  somewhere. The operations runbook says what to check.
- **The polling job no longer crashes** with real mailboxes: they are push-only plus the sweep.

## 3. Results
- Backend tests: **697 passed** (Phase 10: 693). New tests:
  - Graph and Gmail listings read only IDs, across pages, and skip our own sent mail.
  - A lost unsubscribe from an unknown sender is recovered and honoured once; a second sweep and a late push
    change nothing.
  - Only a hash is stored, and the audit holds the count only.
  - A failing mailbox is isolated, and mock mode is skipped.
- Lint, types and the 3 import contracts are clean. On the owner's PC: **697 passed, 0 failed** (§3.1).

### 3.1 On the owner's PC
- Development and demo databases migrated to `0014`; nothing deleted.
- Backend against the PC's Docker PostgreSQL: **697 passed, 0 failed, 0 skipped** (≈ 432 s).
- Local app restarted on the new code: schema 0014, database, Valkey and web page OK.

## 4. Limits
- The sweep looks back 72 hours. An outage longer than that still needs a manual check of the inbox; the alert
  fires long before.
- Messages without a Message-ID header are counted, not handled. This is rare, and those messages are almost
  never replies.
