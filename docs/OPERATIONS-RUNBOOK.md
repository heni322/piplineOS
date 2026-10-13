# Operations runbook

| Alert / situation | What it means | What to do |
|---|---|---|
| `LiveSendWithoutGoLive` | A live send while the checklist is incomplete. The database should make this impossible | **Stop:** set `GLOBAL_GO_LIVE=false`, restart, keep logs and the database as they are, investigate before anything else |
| `GoLiveSwitchedOn` | Global go-live is on | Expected only after the owner switched it on (go-live runbook §C) |
| `InboxPaused` | The kill switch paused an inbox: live hard bounces ≥ 3% or complaints ≥ 0.3% over 7 days (minimum 50 sends) | Find the cause (bad list segment, DNS/DKIM, content). Fix it, then resume in the console with a written reason. Thresholds are in `config/deliverability.yaml` |
| `DataProtectionRequestOverdue` / `DueSoon` | A legal deadline is close or missed | Data requests page: respond (the draft is ready), or record why you refuse. Erasure needs your authenticator code and an identity check |
| `SendsNeedReconciliation` | A send started but its outcome is unknown (crash mid-send) | Check the provider's sent items for that message. Mark it sent or failed. **Never resend blindly** |
| `ReplySlaBreached` | Replies unclassified after 5 minutes | Check the `worker-replies` container and Valkey; `docker compose logs worker-replies` |
| `OutboxStuck` | Events not dispatched | Check `beat` and `worker-default` |
| `AiFailures` | Many failed AI calls | Check the Anthropic status page and the API key / spend cap. Drafts escalate to humans meanwhile |
| `VatThresholdApproaching` | Turnover ≥ 80% of the VAT threshold | Talk to your accountant |
| Graph webhook rejections in the audit log | Notifications with a wrong clientState | Expected noise from scanners. A burst may mean the webhook secret leaked: rotate `MSGRAPH_WEBHOOK_SECRET` and re-subscribe |
| Restore needed | Data loss or corruption | `scripts/restore.sh` into a new database, check it, then point `DATABASE_URL` / `DATABASE_OWNER_URL` at it and restart. Never restore over the live database |
| Lost encryption key | Backups and personal data become unreadable | Prevent it: keys are stored offline in two places (deployment §2). There is no recovery without the key |

**Routine:**
- Weekly: dependency update pull requests; review audit log samples.
- Monthly: restore drill; check the VAT monitor and the open data-protection requests.
- Quarterly: rotate the MS Graph client secret and the metrics token.
