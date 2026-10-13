# ADR-0014 — Phase 6: production readiness

**Status:** Proposed (recorded at end of Phase 6; engineering decisions taken under the delegation in ADR-0011)

| Decision | Why | Reversible |
|---|---|---|
| **Microsoft 365 first, through Microsoft Graph with app-only (client-credentials) auth**, restricted by an Exchange application access policy to the sending mailboxes. Google Workspace is not built yet | Most UK B2B sending inboxes we target are on M365; one provider done properly beats two done halfway | Yes (adapter interface unchanged) |
| **Mail is sent as raw MIME through Graph `sendMail`**, not Graph's JSON message format | The JSON format can't carry `List-Unsubscribe` / `List-Unsubscribe-Post` headers, which the compliance footer and one-click unsubscribe require (D30) | — |
| **Deterministic Message-ID per message, and calendar `transactionId` per meeting** | Retries can't produce duplicate emails or duplicate meetings | — |
| **Inbound replies by Graph change notifications** with a per-mailbox HMAC `clientState`; invalid notifications are rejected and audited; subscriptions renewed by a scheduled task | Real-time replies keep the 5-minute reply SLA; spoofed notifications can't inject replies | Yes |
| **Real adapters are only reachable through the outbound gateway and the three-key switch** (adapter live + tenant live + global go-live with the checklist signed off). Sandbox mode sends only to a local mail sink (Mailpit / localhost) | The brief's dry-run-by-default rule; proven by `test_real_adapter_switch.py` | — |
| **Companies House live adapter is read-only and accepts only an exact normalised-name match**; an ambiguous result returns "unknown", which the compliance engine treats as blocked | Subscriber type decides the legal basis; guessing is not allowed | — |
| **Enrichment and email-verification adapters are not built** until the owner chooses a provider | Each is a paid contract with a data-protection angle; building against an unchosen API is waste | — |
| **System email (invites, password resets, invoice notices) goes through the same three-key switch** and the dry-run log | No path sends real email outside the gate | — |
| **Client accounts are invited by single-use, hashed, expiring tokens; passwords are set through a SECURITY DEFINER function.** The operator never sees a client password (replaces the Phase 5 one-time password, D29) | Least privilege; no shared secrets | — |
| **Deliverability kill switch pauses an inbox automatically** on live hard bounces ≥ 3% or complaints ≥ 0.3% over 7 days (minimum 50 sends); resuming needs a written reason. Bounces skip the reply agent | Protects sending domains and the client's reputation (D28) | Thresholds in `config/deliverability.yaml` |
| **One rate limiter shared across workers (Valkey token bucket), with a local fallback** | Limits hold with several API workers; the app keeps working if Valkey is briefly down | — |
| **Hosting: one VM in a London region with Docker Compose, Caddy for TLS** (option A in `DEPLOYMENT.md`), managed PostgreSQL later | Cheapest sound start; UK data residency | Yes |
| **Backups: nightly `pg_dump` by a dedicated read-only backup role (BYPASSRLS), encrypted with AES-256-GCM (scrypt key), off-site copy by command; restore only into a new database; monthly restore drill.** Nothing is deleted unless `RETENTION_DAYS` is set | Forced RLS makes a normal dump empty; never overwriting the live database matches the no-deletion rule | — |
| **Metrics behind a bearer token, aggregate counts only; logs carry no personal data** | Observability without a PII leak | — |
| **AI prices from Anthropic's published list in USD, converted with an FX rate the owner sets** (null until set → reports say "not configured") | Honest cost reporting; the exchange rate is a business choice | Yes (config) |
| **CI: lint, types, contracts, tests, dependency audit, gitleaks over full history, Trivy; Dependabot weekly** | No new high/critical vulnerabilities, no committed secrets | — |
