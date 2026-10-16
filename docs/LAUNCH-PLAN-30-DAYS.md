# 30-day launch plan

Each line says who acts. "Me" means work I do in this repository when you tell me the step is done.

| Day | You | Me | Done when |
|---|---|---|---|
| 1 | Merge `phase/17-business-readiness` into main | — | PR merged |
| 1 | Email the solicitor (`docs/SOLICITOR-BRIEF.md`) | — | Quote received |
| 1–2 | Buy a VPS (2 vCPU / 4 GB minimum; 8 GB if the local AI model runs on it), a sending domain (e.g. `get<yourbrand>.co.uk`, **not** your main domain) and the main app domain | — | Accounts exist |
| 2 | Pay the ICO data protection fee; fill `config/supplier.yaml` (company details) | Check the readiness page turns green for those items | Go-live page shows "Company details: Complete" |
| 3 | Deploy staging, then production (`docs/DEPLOYMENT.md` §7) | Help over the session if anything fails | `/api/v1/ready` returns ready |
| 3 | Create **3** mailboxes on the sending domain (Microsoft 365 or Google Workspace). Three, because a 300-person pilot needs about 880 sends in 14 days, follow-ups included | — | Mailboxes log in |
| 3 | In the console: add your own outreach account and the sending domain; publish SPF, DKIM, DMARC | Check DNS results with you | Inboxes page: SPF, DKIM, DMARC pass |
| 3 | Run `pipelineos signals check …` on the server for each free source | Fix any source whose real format differs | Source health shows "ok" for each |
| 3–10 | Contact your first 10 MSPs (`docs/FIRST-CLIENT-KIT.md`) | — | 3–5 calls booked |
| 4–24 | Warm the mailboxes **by hand**: use them for normal emails with people you know (a few a day, rising). PipelineOS counts only its own live sends, so this happens outside it | — | Replies coming back, nothing in spam |
| ~10–14 | Solicitor's answers arrive | Record the review, put the approved texts in, set retention, apply changes, all tested | Go-live checklist signable |
| ~14 | First pilot signed | Onboard with you | Client "active" |
| 24 | Run the placement check (`docs/GO-LIVE-RUNBOOK.md` §C2); sign the go-live checklist; set `GLOBAL_GO_LIVE=true`; set the pilot client live | Watch the first sends with you | First live email delivered, unsubscribe tested |
| 24–38 | Pilot: approve every email, reply within a day, watch bounces and complaints. PipelineOS ramps each inbox itself, from 10 a day rising by 2 a day. About 880 sends are needed for 300 people with 2 follow-ups; 3 inboxes give about 970 in 14 working days, 2 inboxes only about 640 (then plan about 3½ weeks) | Fix anything that comes up | Day-5 and day-14 thresholds (GO-TO-MARKET §6) |
| 38 | Pilot review; first invoice issued and paid by bank transfer | Record the payment | Invoice "paid" |

## What the score becomes at each milestone (honest)

| Milestone | Score |
|---|---|
| Today | 5.1 |
| Solicitor's review recorded | about 6 |
| Domain, mailboxes and warm-up done; sources checked live | about 6.5 |
| Pilot run, with real numbers per 100 reached | about 7–7.5 |
| First invoice paid; production restore drill done | about 8 |
| External penetration test; a second client | about 8.5–9 |
