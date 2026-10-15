"""The economics model: internally consistent, reproducible, and honest about small numbers."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.economics.simulator import SCENARIOS, expected, leads_needed, monte_carlo, report


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_funnel_only_narrows(name: str) -> None:
    r = expected(SCENARIOS[name], 1000)
    chain = [
        r.leads,
        r.passed_compliance,
        r.contactable,
        r.approved,
        r.reached,
        r.replies,
        r.positive,
        r.booked,
        r.attended,
        r.qualified,
    ]
    assert chain == sorted(chain, reverse=True)
    assert r.contactable + r.published_contact_tasks == pytest.approx(r.passed_compliance, abs=0.2)
    assert r.emails_sent >= r.approved  # follow-ups only add emails


def test_scenarios_are_ordered() -> None:
    q = [expected(SCENARIOS[k], 10_000).qualified for k in ("pessimistic", "base", "optimistic")]
    assert q == sorted(q)


def test_revenue_scales_with_price_and_retainer() -> None:
    a = SCENARIOS["base"]
    r1 = expected(a, 1000)
    r2 = expected(replace(a, price_per_meeting_eur=600, monthly_retainer_eur=500), 1000)
    assert r2.revenue_eur == pytest.approx(2 * r1.revenue_eur + 500, abs=0.05)
    assert r2.cost_eur == r1.cost_eur


def test_leads_needed_inverts_the_funnel() -> None:
    a = SCENARIOS["base"]
    n = leads_needed(a, 10)
    assert expected(a, n).qualified == pytest.approx(10, abs=0.05)


def test_monte_carlo_is_reproducible_and_shows_the_spread() -> None:
    a = SCENARIOS["base"]
    one, two = monte_carlo(a, runs=300), monte_carlo(a, runs=300)
    assert one == two
    q = one["qualified_meetings"]
    assert q["p10"] <= q["p50"] <= q["p90"]
    assert 0 <= one["chance_of_zero_qualified"] <= 1


def test_report_covers_every_volume_and_target() -> None:
    out = report(volumes=(100, 1000))
    assert set(out["volumes"]) == set(SCENARIOS)
    assert [r["leads"] for r in out["volumes"]["base"]] == [100, 1000]
    assert set(out["revenue_targets"]["base"]) == {"EUR 1,000/month", "EUR 10,000/month", "EUR 100,000/month"}
