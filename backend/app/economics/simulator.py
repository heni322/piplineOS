"""Unit economics and funnel simulation (pure: no database, no network, no clock).

Every rate below is an ASSUMPTION, not a measurement. PipelineOS has no real-world pilot yet, so none of these
numbers is validated; they come from public cold-email benchmarks for B2B outreach and from how this system is
built (free sources only, human approval of every message, 50 emails/inbox/day cap, warm-up). Replace them with
measured values after the first pilot. The point of the model is to show which assumptions decide the business
(contactable rate, positive-reply rate, show rate, review time), not to promise an outcome.

Funnel (per lead discovered from a public signal):
  discovered -> passes the compliance gate -> has a usable business email (free sources rarely find one) ->
  approved by a human -> first email delivered (not bounced) -> replies -> positive -> meeting booked ->
  attended -> qualified (meets the client's signed criteria) = billable.
Leads with no email become human "published contact" tasks (phone/form/letter): counted as work, not meetings.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field, replace
from typing import Any

EMAILS_PER_INBOX_DAY = 40  # warm target cap (config/warmup.yaml target_cap); the hard limit is 50
WARMUP_DAYS = 21  # days of live sending before an inbox is "active" (config/warmup.yaml)


@dataclass(frozen=True)
class Assumptions:
    name: str
    # funnel rates
    compliance_pass: float  # discovered leads that pass country/role/legal-form/suppression checks
    contactable: float  # of those, a usable business email exists in free sources (website, the prospect's reply)
    approved: float  # human approves the draft
    bounce: float  # hard bounce rate on first emails
    reply: float  # any reply, per person reached (whole sequence)
    positive_of_replies: float  # interested
    booked_of_positive: float
    show: float
    qualified_of_attended: float  # meets the client's signed criteria -> billable
    follow_ups: int = 2  # sequence follow-ups for non-repliers (config Sequence.follow_ups default)
    # operator effort (minutes)
    review_min_per_draft: float = 1.0
    reply_min_per_reply: float = 4.0
    task_min_per_published_contact: float = 3.0  # published-contact tasks are optional manual work...
    tasks_worked: float = 0.1  # ...done only for the best-scored leads; the rest wait (no meetings counted from them)
    meeting_admin_min: float = 10.0
    # money (EUR)
    hourly_cost_eur: float = 30.0  # operator time, the founder's or a contractor's
    price_per_meeting_eur: float = 300.0  # matches the demo pricing (£300) for a qualified, attended meeting
    monthly_retainer_eur: float = 0.0
    setup_fee_eur: float = 0.0
    fixed_monthly_eur: float = 60.0  # VPS + backups + monitoring (free software stack, local LLM)
    per_inbox_monthly_eur: float = 7.0  # Microsoft 365 / Google Workspace seat
    per_domain_yearly_eur: float = 12.0
    inboxes_per_domain: int = 2
    llm_cost_per_lead_eur: float = 0.0  # local model: electricity only


SCENARIOS: dict[str, Assumptions] = {
    "pessimistic": Assumptions(
        "pessimistic",
        0.65,
        0.12,
        0.85,
        0.05,
        0.02,
        0.25,
        0.40,
        0.65,
        0.60,
        review_min_per_draft=1.5,
        reply_min_per_reply=6.0,
    ),
    "base": Assumptions("base", 0.75, 0.25, 0.90, 0.03, 0.04, 0.35, 0.55, 0.75, 0.75),
    "optimistic": Assumptions(
        "optimistic",
        0.85,
        0.40,
        0.95,
        0.015,
        0.07,
        0.45,
        0.70,
        0.85,
        0.85,
        review_min_per_draft=0.5,
        reply_min_per_reply=3.0,
    ),
}


@dataclass
class Result:
    scenario: str
    leads: int
    passed_compliance: float
    contactable: float
    published_contact_tasks: float
    approved: float
    reached: float
    bounced: float
    emails_sent: float
    replies: float
    positive: float
    booked: float
    attended: float
    qualified: float
    per_100_reached: dict[str, float | None] = field(default_factory=dict)
    inboxes_needed_30_days: int = 0
    warmup_lead_time_days: int = WARMUP_DAYS
    sending_days_with_2_inboxes: float = 0.0
    operator_hours: float = 0.0
    revenue_eur: float = 0.0
    cost_eur: float = 0.0
    margin_eur: float = 0.0
    cost_per_qualified_meeting_eur: float | None = None
    leads_per_qualified_meeting: float | None = None


def _per100(n: float, base: float) -> float | None:
    return round(100 * n / base, 2) if base else None


def expected(a: Assumptions, leads: int, months: float | None = None) -> Result:
    """Expected values for `leads` discovered leads in a month (costs for `months`, default one)."""
    passed = leads * a.compliance_pass
    contactable = passed * a.contactable
    tasks = passed - contactable
    approved = contactable * a.approved
    bounced = approved * a.bounce
    reached = approved - bounced
    replies = reached * a.reply
    # Non-repliers get the follow-ups: emails = first emails + follow-ups to those who have not replied.
    emails = approved + (reached - replies) * a.follow_ups
    positive = replies * a.positive_of_replies
    booked = positive * a.booked_of_positive
    attended = booked * a.show
    qualified = attended * a.qualified_of_attended

    # `leads` is a MONTHLY volume: costs are one month of sending with enough warmed inboxes for it. A new inbox
    # needs WARMUP_DAYS of low-volume live sending first: that is lead time before the first month, reported apart.
    inboxes_30 = max(1, math.ceil(emails / (EMAILS_PER_INBOX_DAY * 21)))  # 21 working days a month
    days_two_inboxes = emails / (2 * EMAILS_PER_INBOX_DAY)
    run_months = months if months is not None else 1.0
    inboxes = max(2, inboxes_30)
    domains = math.ceil(inboxes / a.inboxes_per_domain)
    minutes = (
        approved / a.approved * a.review_min_per_draft  # every draft is reviewed, approved or not
        + replies * a.reply_min_per_reply
        + tasks * a.tasks_worked * a.task_min_per_published_contact
        + booked * a.meeting_admin_min
    )
    hours = minutes / 60
    cost = (
        run_months * (a.fixed_monthly_eur + inboxes * a.per_inbox_monthly_eur)
        + domains * a.per_domain_yearly_eur
        + hours * a.hourly_cost_eur
        + leads * a.llm_cost_per_lead_eur
    )
    revenue = qualified * a.price_per_meeting_eur + run_months * a.monthly_retainer_eur + a.setup_fee_eur
    return Result(
        scenario=a.name,
        leads=leads,
        passed_compliance=round(passed, 1),
        contactable=round(contactable, 1),
        published_contact_tasks=round(tasks, 1),
        approved=round(approved, 1),
        reached=round(reached, 1),
        bounced=round(bounced, 1),
        emails_sent=round(emails, 1),
        replies=round(replies, 2),
        positive=round(positive, 2),
        booked=round(booked, 2),
        attended=round(attended, 2),
        qualified=round(qualified, 2),
        per_100_reached={
            "replies": _per100(replies, reached),
            "positive_replies": _per100(positive, reached),
            "meetings_booked": _per100(booked, reached),
            "qualified_meetings": _per100(qualified, reached),
        },
        inboxes_needed_30_days=inboxes_30,
        sending_days_with_2_inboxes=round(days_two_inboxes, 1),
        operator_hours=round(hours, 1),
        revenue_eur=round(revenue, 2),
        cost_eur=round(cost, 2),
        margin_eur=round(revenue - cost, 2),
        cost_per_qualified_meeting_eur=round(cost / qualified, 2) if qualified else None,
        leads_per_qualified_meeting=round(leads / qualified, 1) if qualified else None,
    )


def _binom(rng: random.Random, n: int, p: float) -> int:
    return sum(1 for _ in range(n) if rng.random() < p)


def monte_carlo(a: Assumptions, leads: int = 1000, runs: int = 2000, seed: int = 7) -> dict[str, Any]:
    """Synthetic leads through the funnel, `runs` times: how much the outcome varies at pilot scale. Small
    numbers swing a lot, which is why a 14-day pilot can't prove or disprove the model on its own."""
    rng = random.Random(seed)  # noqa: S311 - a reproducible simulation, not security
    qualified, booked = [], []
    for _ in range(runs):
        n = _binom(rng, leads, a.compliance_pass)
        n = _binom(rng, n, a.contactable)
        n = _binom(rng, n, a.approved)
        n -= _binom(rng, n, a.bounce)
        n = _binom(rng, n, a.reply)
        n = _binom(rng, n, a.positive_of_replies)
        b = _binom(rng, n, a.booked_of_positive)
        q = _binom(rng, _binom(rng, b, a.show), a.qualified_of_attended)
        booked.append(b)
        qualified.append(q)
    qualified.sort()
    booked.sort()

    def pct(xs: list[int], p: float) -> int:
        return xs[min(len(xs) - 1, int(p * len(xs)))]

    return {
        "scenario": a.name,
        "leads": leads,
        "runs": runs,
        "qualified_meetings": {"p10": pct(qualified, 0.1), "p50": pct(qualified, 0.5), "p90": pct(qualified, 0.9)},
        "meetings_booked": {"p10": pct(booked, 0.1), "p50": pct(booked, 0.5), "p90": pct(booked, 0.9)},
        "chance_of_zero_qualified": round(sum(1 for q in qualified if q == 0) / runs, 3),
    }


def leads_needed(a: Assumptions, qualified_meetings: float) -> int:
    per_lead = (
        a.compliance_pass
        * a.contactable
        * a.approved
        * (1 - a.bounce)
        * a.reply
        * a.positive_of_replies
        * a.booked_of_positive
        * a.show
        * a.qualified_of_attended
    )
    return math.ceil(qualified_meetings / per_lead) if per_lead else 0


def report(volumes: tuple[int, ...] = (100, 1_000, 10_000, 100_000), **overrides: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"assumptions": {}, "volumes": {}, "monte_carlo_1000": {}, "revenue_targets": {}}
    for key, base in SCENARIOS.items():
        a = replace(base, **overrides) if overrides else base
        out["assumptions"][key] = asdict(a)
        out["volumes"][key] = [asdict(expected(a, v)) for v in volumes]
        out["monte_carlo_1000"][key] = monte_carlo(a)
        out["revenue_targets"][key] = {
            f"EUR {target:,}/month": {
                "qualified_meetings": math.ceil(target / a.price_per_meeting_eur),
                "leads_discovered_per_month": leads_needed(a, math.ceil(target / a.price_per_meeting_eur)),
            }
            for target in (1_000, 10_000, 100_000)
        }
    return out
