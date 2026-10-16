# Operations runbook

| Alert / situation | What it means | What to do |
|---|---|---|
| `LiveSendWithoutGoLive` | A live send while the checklist is incomplete. The database should make this impossible | **Stop:** set `GLOBAL_GO_LIVE=false`, restart, keep logs and the database as they are, investigate before anything else |
| `GoLiveSwitchedOn` | Global go-live is on | Expected only after the owner switched it on (go-live runbook §C) |
| `InboxPaused` | The kill switch paused an inbox: live hard bounces ≥ 3% or complaints ≥ 0.3% over 7 days (minimum 50 sends) | Find the cause (bad list segment, DNS/DKIM, content). Fix it, then resume in the console with a written reason. Thresholds are in `config/deliverability.yaml` |
| `ClientStoppedSending` | A client pressed **Stop all sending** in their portal. Everything of theirs is held, nothing is lost | Call the client the same day. Read the reason on the Clients page. Resume only after you agree what changes; the resume is audited |
| `PilotLimitReached` | A campaign emailed as many new people as its pilot limit allows. Follow-ups continue; new first emails wait | Review the pilot numbers (Reports: per 100 people reached, bounces, complaints). Raise the limit on the Campaigns page only when the thresholds in `docs/GO-TO-MARKET.md` §6 are met |
| `PilotRequestWaiting` | A company asked for a pilot on the landing page more than an hour ago | Pilot requests page: reply by email (they opted in), then mark it **Contacted**. Aim to answer within two working days, as the form promises. If they ask to be forgotten: **Delete** on that page erases the request for real |
| `InvoiceOverdue` | An issued invoice is past its due date and not fully paid | Check the bank statement. If the money arrived, record it on the Billing page (your authenticator code is needed). If not, remind the client. A mistaken entry is corrected with a **reversal**, never edited |
| Emergency stop (operator) | You need everything for one client to stop now | Clients page → **Pause (emergency stop)** with a reason. For one campaign: Campaigns page → Pause. Messages already handed to the mail provider cannot be recalled |
| `DataProtectionRequestOverdue` / `DueSoon` | A legal deadline is close or missed | Data requests page: respond (the draft is ready), or record why you refuse. Erasure needs your authenticator code and an identity check |
| `SendsNeedReconciliation` | A send started but its outcome is unknown (crash mid-send) | Check the provider's sent items for that message. Mark it sent or failed. **Never resend blindly** |
| `ReplySlaBreached` | Replies unclassified after 5 minutes | Check the `worker-replies` container and Valkey; `docker compose logs worker-replies` |
| `RepliesRecoveredBySweep` | The 15-minute sweep found replies that never arrived by push. They were handled normally (unsubscribes honoured), but the push path is broken somewhere | Check the webhook endpoint is reachable (`/webhooks/msgraph`, `/webhooks/google`), that the subscriptions or watches are renewing (`renew_*` tasks in `docker compose logs beat`), and the provider's status page. The sweep looks back 72 hours, so fix it within that window |
| `OutboxStuck` | Events not dispatched | Check `beat` and `worker-default` |
| `OutboxDeadLetter` | An event failed all 10 attempts (about half an hour) and is no longer retried | `docker compose logs worker-default \| grep outbox_dead_lettered` gives its id and topic. Fix the cause, then `UPDATE outbox SET attempts = 0, next_attempt_at = NULL WHERE id = '<id>'` to retry it. The lead it concerns is also picked up by the next hunt run |
| `SendsFailing` | An email failed for good: the provider refused it (4xx), or 6 attempts over about an hour were all refused. Nothing was sent. A `send_failed` escalation holds the error | Most often the mailbox's connection: an expired or revoked consent, a disabled mailbox, a missing licence. Fix it on the Inboxes page. To send it after all: `UPDATE messages SET status = 'approved', send_attempts = 0, next_attempt_at = NULL WHERE id = '<message_id>'` (the id is in the escalation). The send-time compliance check runs again |
| `ProviderRefusingSends` | Over 20 emails are backing off because Microsoft or Google is refusing them (rate limit or outage) | Nothing is lost and nothing is sent twice: each retries with backoff (Retry-After honoured), and that inbox pauses for the rest of the tick. Check the provider's status page. If it lasts more than an hour, the messages fail and escalate (`SendsFailing`) |
| `AiFailures` | Many failed AI calls | Check the local model (`docker compose ps ollama`) or the API status and key. While the model is unreachable, its circuit opens for 2 minutes at a time. Drafts **wait** for the next run (no escalation), unsubscribes are still honoured by the rules, other replies go to a person (ADR-0019) |
| `VatThresholdApproaching` | Turnover ≥ 80% of the VAT threshold | Talk to your accountant |
| Graph webhook rejections in the audit log | Notifications with a wrong clientState | Expected noise from scanners. A burst may mean the webhook secret leaked: rotate `MSGRAPH_WEBHOOK_SECRET` and re-subscribe |
| Restore needed | Data loss or corruption | `scripts/restore.sh` into a new database, check it, then point `DATABASE_URL` / `DATABASE_OWNER_URL` at it and restart. Never restore over the live database |
| Lost encryption key | Backups and personal data become unreadable | Prevent it: keys are stored offline in two places (deployment §2). There is no recovery without the key |

**Routine:**
- Weekly: dependency update pull requests; review audit log samples.
- Monthly: restore drill; check the VAT monitor and the open data-protection requests.
- Quarterly: rotate the MS Graph client secret and the metrics token.
