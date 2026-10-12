# PipelineOS demo script (Phase 3, synthetic data, dry-run)

For showing a prospective client how the service works. Takes about 10 minutes. Everything is fictional
(`.example` domains) and nothing can leave the system: no email, no booking, no paid AI call.

## Setup (once, on the demo machine)

```powershell
./scripts/dev-setup.ps1                      # database, keys, config
cd backend
uv run pipelineos demo seed                  # prints the demo operator login + authenticator QR link ONCE
uv run pipelineos demo run                   # agents find signals, contacts and draft emails
uv run uvicorn app.main:app --port 8000      # API
cd ../frontend; pnpm build; pnpm start       # web on http://localhost:3000
```

Add the printed `otpauth://` link to an authenticator app, then sign in at http://localhost:3000/login.

## The story to tell

1. **Overview.** "Fourteen public signals this month: contract awards, new hires, funding. Twelve matched the
   ideal customer profile; a bakery didn't, and a duplicate posting was ignored." Point at *Real emails sent: 0*.
2. **Compliance log.** Walk the outcomes:
   - German IT director at a UK company → stricter German rule → *no email*, a human-only task instead.
   - Sole trader → treated as an individual under PECR → *no email*.
   - Dublin company → Ireland not enabled for email → blocked until legal review.
   - Someone who objected to another campaign → suppressed everywhere, permanently.
   - Unverifiable email or unknown company type → blocked rather than guessed.
3. **Approvals.** Open one email. "Every fact is highlighted and numbered; click the number to see the source.
   The footer with company details, data source and one-click unsubscribe is added automatically."
   Approve with **A**. Show editing (needs a logged reason to reveal the name) and rejecting with a reason.
4. **Prompt injection.** Open the Proseware email: its job ad told any AI to score it 100 and add a payment link.
   The instruction was stripped before the AI saw it, and the email contains no link.
5. **Dry-run log.** After `POST /api/v1/send/run` (or `uv run pipelineos demo send`), the would-be email appears
   here with the recipient masked, during the recipient's business hours only.

## What to say about AI

In the demo the agents run on a built-in simulator (recorded as `simulated` in the AI-call log). Live AI is enabled
only for a live client after go-live sign-off, under the same three-switch rule as sending.
