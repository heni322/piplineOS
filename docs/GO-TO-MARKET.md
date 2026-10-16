# PipelineOS: go-to-market, pricing and unit economics (2026-10-15)

All figures below are **assumptions until a pilot measures them**. The model behind the numbers is
`pipelineos economics simulate` (`backend/app/economics/simulator.py`). Change the inputs there, not here.

## 1. What we sell

**Qualified first meetings for UK B2B service companies.** Each meeting is:
- found from a **public buying signal**: a contract award, hiring, a new director, a funding filing;
- reached with one honest, specific email;
- booked into the client's calendar.

The client approves every email during the pilot, and pays for meetings that took place with a buyer who meets
the criteria they signed.

## 2. Business models compared

All five assume the base case of §8: 1,000 leads a month gives about 0.7 qualified meetings.

| Model | How the client pays | Our margin (base) | Client risk | Our risk | Chance of a FIRST customer | Verdict |
|---|---|---|---|---|---|---|
| **A. Pay per meeting** | £/€300 per qualified, attended meeting | **Negative** (−€71/month per client at 1k leads) | None | All of it; disputes decide our revenue | High | Unsustainable on free data at base rates |
| **B. SaaS** | €99–499/month, client runs it | High | Effort + outcome | Low | **Low**: the client must operate approvals, inboxes, legal | Not ready: no self-serve onboarding, and every client needs their own legal review |
| **C. Base + performance** | Fixed monthly fee + per qualified meeting | **Positive in all three cases** (€530–1,955/month) | Moderate, capped by the pilot | Moderate | **Good**, if the fixed part is small and the pilot is short | **Recommended** |
| **D. Managed service** | €2–5k/month retainer, done for you | High | High upfront | Low | Low for an unproven supplier | Later, once results exist |
| **E. Enterprise** | Annual contract, SSO, DPA negotiation | High | Low | Long sales cycle | Very low without proof or certifications | Later |

### Recommendation: Model C, sold as a priced pilot

| Item | Pilot (first 4–6 weeks) | After the pilot |
|---|---|---|
| Fixed | **£/€490 once** (domain, 3 mailboxes, DNS, warm-up, set-up, 300 contacts) | **£/€750 / month** |
| Per qualified, attended meeting | **£/€250** | **£/€250–300** |
| Guarantee | If fewer than 3 positive replies from 300 people reached, the fixed fee is credited to month 1 or refunded (`LEGAL REVIEW REQUIRED`: refund wording) | none |

**Why this gives the best chance of a first customer:**
- **The client's maximum exposure is £/€490 plus meetings they would want anyway.** That is less than one day of
  a salesperson.
- **The fixed part covers our real cost.** The model gives about €280–320/month at 1k leads, almost all mailboxes
  and operator time. So a pessimistic pilot costs us time, not cash.
- **The per-meeting part keeps us honest.** It uses the billing rules already built: attendance confirmed by the
  calendar, the CRM or the client, criteria signed before booking, and a dispute window.

**Watch out.** In the pessimistic case the *client* loses: €765 for 0.05 meetings. That is why the pilot has a
stop rule (§6) and the guarantee, and why nobody moves to the monthly fee without pilot evidence.

## 3. Ideal customer profiles

| | ICP | Why they buy | Signals we already read | Decision-maker | Deal value to them |
|---|---|---|---|---|---|
| **#1 (beachhead)** | **UK IT managed service providers, 10–200 staff**, selling to UK SMEs and public-sector suppliers | Growth depends on new contracts; founders sell themselves and lack time | Contract awards (Contracts Finder, Find a Tender), hiring for IT roles, new directors, funding filings | Founder / MD (in Companies House officers) | £20–100k per year per new client |
| #2 | UK cyber-security consultancies and MSSPs | Same buyers, higher deal values, compliance-minded (they value our safety) | Awards, funding (SH01), new IT/finance directors | Founder / Head of sales | £30–150k |
| #3 | UK IT recruitment agencies | Hiring signals are exactly their trigger | Job boards (Greenhouse, Lever, Ashby) of watched companies | Director | £10–25k per placement |

**Beachhead: #1.** The demo configuration and writer prompts already target it (`UK IT MSPs`). The signals are
public and specific. In small companies the directors in the register are the buyers.

## 4. Customer journey

| Step | Built? | Where |
|---|---|---|
| 1. Finds us | **Landing page** (new) | `/` |
| 2. Pilot call | human | owner |
| 3. Contract and DPA signed | records only | Clients page: LIA, DPA (`LEGAL REVIEW REQUIRED`: the documents themselves) |
| 4. Account created, client signs criteria and pricing | yes | Clients page → portal Terms |
| 5. Targeting (keywords, SIC, watchlist) | yes | Clients → Targeting |
| 6. Domain + mailboxes + warm-up | DNS check and warm-up built; buying them is human | Inboxes page |
| 7. Activation (checklist complete) | yes | Clients → Activate |
| 8. Pilot: campaign capped at 100 by default, every email approved | **yes (new)** | Campaigns page, Approvals |
| 9. Emergency stop by operator or client | **yes (new)** | Clients, Campaigns, portal |
| 10. Replies handled, meetings booked | yes (dry-run) | Escalations, Meetings |
| 11. Results per 100 reached + ROI | **yes (new)** | portal Overview, Reports |
| 12. Billable meetings with evidence, dispute | yes | portal Meetings |
| 13. Invoice | draft only; issuing needs your sign-offs | Billing |
| 14. Payment | **missing** (bank transfer by hand) | n/a |
| 15. Proof / case study | **missing** | n/a |
| 16. Renewal / monthly plan | **missing** (manual) | n/a |

## 5. Deliverability plan (meetings per 100 delivered)

- **Before the first send:**
  - a separate sending domain (never the client's main domain);
  - SPF, DKIM, DMARC at `p=none`, then `quarantine`;
  - 3 mailboxes for a 300-person pilot in 14 days (see the capacity note in §6);
  - 21 days of warm-up starting at 10 a day (`config/warmup.yaml`).
- **During the pilot:**
  - at most 40 a day per warmed inbox (hard cap 50), business hours only;
  - automatic pause at 3% hard bounces or 0.3% complaints (`config/deliverability.yaml`).
- **What moves meetings per 100:**
  - a real, recent signal in line 1;
  - a named person in the buyer persona;
  - one question, plain text, no links but the unsubscribe;
  - 2 follow-ups at most.
- **These levers are already enforced by the writer and critique steps. Their effect is NOT yet measured.**

## 6. The 14-day pilot (after a 21-day warm-up)

A 100-person pilot reaches about 100 people. Using the per-reached rate of §8, the chance of **at least one
qualified meeting** is:

| People reached | Pessimistic | Base | Optimistic |
|---|---|---|---|
| 100 | 8% | 35% | 80% |
| 300 | 21% | **73%** | 99% |
| 500 | 32% | 89% | 100% |

So **100 people proves deliverability and reply quality, not meeting economics. Sell 300.**

**Capacity check.**
- 300 people with up to 2 follow-ups is about 880 sends. Each inbox starts at 10 a day and rises by 2 a day
  (`config/warmup.yaml`), and follow-ups go first.
- In 14 working days, 2 inboxes send about 640 and 3 inboxes about 970.
- So a 14-day pilot of 300 needs **3 inboxes**. With 2, plan about 3½ weeks. That is about
1,800 leads discovered at the base funnel.

| Day | What happens | Pass threshold |
|---|---|---|
| −21 | Domain and mailboxes live, warm-up starts | DNS all green on the Inboxes page |
| 0 | Campaign active, Pilot Mode limit 300 | Checklist complete |
| 1–5 | Each inbox starts at 10 emails a day and rises by 2 a day; follow-ups go first. About 30–50 first emails a day across 3 inboxes, all approved | Bounces < 3%, complaints 0 |
| 5 | Checkpoint | ≥ 1 reply per 100 reached; otherwise change the copy |
| 6–12 | Follow-ups; replies handled | Positive replies ≥ 1.0 per 100 reached |
| 14 | Review with the client | **Success:** ≥ 1 qualified meeting OR ≥ 3 positive replies. **Stop:** complaint rate ≥ 0.3%, or 0 positive replies from 300 |

## 7. The real bottleneck: contactable rate

With free sources only:
- register officers have **no email address**, and none is ever guessed;
- addresses come only from the company's own published website or the prospect's reply.

The model assumes 25% of compliant leads are emailable (base). **It is the single biggest lever:** going from 25%
to 40% roughly doubles meetings. The options, all **owner decisions**:
1. **The client supplies its own target accounts and contacts** (from its CRM, as controller). Fastest; their
   lawful basis.
2. **Published-contact tasks:** a person calls or fills a contact form. Slow, but legal and already built.
3. **A paid data provider:** built behind the paid-call gate (`FREE_ONLY=false`). It breaks the "free only" rule,
   so it is your call.

## 8. Unit economics (per client-month, 1,000 leads discovered)

The defaults:
- £/€300 a meeting;
- €30/h operator time;
- €60/month server;
- €7 per mailbox;
- local AI (no API cost);
- every draft reviewed.

| | Pessimistic | Base | Optimistic |
|---|---|---|---|
| People reached | 63 | 164 | 318 |
| Qualified meetings | 0.05 | 0.71 | 5.1 |
| Leads per qualified meeting | 20,355 | 1,411 | 197 |
| Operator hours | 5.0 | 6.6 | 7.7 |
| Our cost | €235 | €284 | €316 |
| Revenue, model A (per meeting only) | €15 | €213 | €1,521 |
| **Margin, model A** | **−€220** | **−€71** | €1,205 |
| Revenue, model C (€750 + per meeting) | €765 | €963 | €2,271 |
| **Margin, model C** | €530 | €679 | €1,955 |
| Cost per qualified meeting | €4,777 | €400 | €62 |

The 1,000-lead Monte Carlo (2,000 runs) gives qualified meetings at P10/P50/P90 of:
- pessimistic: 0 / 0 / 0;
- base: 0 / 1 / 2;
- optimistic: 2 / 5 / 8.

**At pilot scale the result is mostly luck.** Judge the pilot on replies and positives per 100 reached, which come
in larger numbers.

## 9. €1k / €10k / €100k a month

Model C: €750 a month plus €300 a meeting.

| Target | Clients | What it takes | Main constraint |
|---|---|---|---|
| **€1k / month** | 1 client (€750) + 1 meeting | 1,000–2,000 leads a month, 2 inboxes, ~8 h a month of your time | The first client and the solicitor |
| **€10k / month** | 8–10 clients | ~10k leads a month, ~20 inboxes on ~10 domains, ~70 h a month of review and replies (half an FTE) | Contactable rate, approval time (move to sampled approval after the 14-day lock), deliverability at scale |
| **€100k / month** | ~80–100 clients, or fewer on model D | ~100k leads a month, 100+ inboxes, a team of 3–5 (ops, deliverability, CS), likely a paid data source, SOC 2-style controls | People, data sourcing, and a server per region |

## 10. Risk register

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| 1 | UK outreach to corporate subscribers ruled out of scope by counsel (sole traders and partnerships count as individuals) | Medium | High | Subscriber-type check already blocks non-corporates; solicitor review | Solicitor |
| 2 | Complaint to the ICO | Low–Medium | High | Suppression, one-click unsubscribe, notice, LIA, emergency stop, low volume | Owner |
| 3 | Domain blacklisted | Medium | Medium | Separate domains, warm-up, kill switch, caps | Owner |
| 4 | Too few emailable contacts (free-only) | **High** | High | §7 options | Owner |
| 5 | Pilot shows 0 meetings by chance | Medium | High | Sell 300 contacts; judge on replies per 100 | Owner |
| 6 | Billing dispute over "qualified" | Medium | Medium | Signed criteria, evidence chain, dispute window, attendance confirmed by client/calendar | Built |
| 7 | AI writes something false | Low | High | Sourced claims only, critique, human approval of every email in the pilot | Built |
| 8 | Data breach / cross-client leak | Low | Very high | Forced RLS, encryption, MFA, audit; external pen test still missing | Pen tester |
| 9 | Founder time | High | Medium | Sampled approval after the lock period, simple client set | Owner |
| 10 | Local model too slow on a small VPS | Medium | Low | Measured ~16 s a reply on the owner's PC; size the VPS or keep the AI on the PC | Owner |

## 11. PipelineOS selling itself

- The schema already supports the operator's own tenant: `is_operator_own`, role `controller`.
- Running PipelineOS on ICP #1 for our own pilots is the cheapest proof. It needs the same solicitor review, our
  own LIA, notice and sending domain. **`LEGAL REVIEW REQUIRED`.**
