"""Billing eligibility, tax selection and arithmetic (pure), incl. property tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.billing.eligibility import MeetingFacts, eligibility
from app.domain.billing.service import BillingError, money, month_bounds
from app.domain.billing.tax import TaxRuleRow, TaxUnresolved, select_rule, tax_facts, tax_minor
from app.domain.tenants.config import Pricing, Qualification
from app.domain.tenants.signoffs import terms_hash

BOOKED = datetime(2026, 10, 13, 12, tzinfo=UTC)


def facts(**kw) -> MeetingFacts:  # type: ignore[no-untyped-def]
    base = dict(
        booked_by_system=True,
        meeting_status="attended",
        attendance_status="attended",
        attendance_source="calendar",
        qualification_met=True,
        criteria_hash="h1",
        booked_at=BOOKED,
        signed_criteria=[("h1", BOOKED - timedelta(days=3))],
        already_billed=False,
        per_meeting_minor=30_000,
        pricing_signed=True,
        dispute_status=None,
    )
    base.update(kw)
    return MeetingFacts(**base)  # type: ignore[arg-type]


def test_a_complete_evidence_chain_is_billable() -> None:
    assert eligibility(facts()) == (True, [])


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"booked_by_system": False}, "NOT_BOOKED_BY_SYSTEM"),
        ({"meeting_status": "no_show", "attendance_status": "no_show"}, "NOT_ATTENDED"),
        ({"attendance_source": "operator"}, "ATTENDANCE_NOT_CONFIRMED_BY_CLIENT_OR_CALENDAR"),
        ({"qualification_met": False}, "NOT_QUALIFIED"),
        ({"qualification_met": None}, "NOT_QUALIFIED"),
        ({"criteria_hash": "h2"}, "CRITERIA_NOT_SIGNED_AT_BOOKING"),
        ({"signed_criteria": [("h1", BOOKED + timedelta(minutes=1))]}, "CRITERIA_NOT_SIGNED_AT_BOOKING"),
        ({"signed_criteria": []}, "CRITERIA_NOT_SIGNED_AT_BOOKING"),
        ({"already_billed": True}, "ALREADY_BILLED"),
        ({"per_meeting_minor": 0}, "NO_PER_MEETING_FEE"),
        ({"pricing_signed": False}, "PRICING_NOT_SIGNED"),
        ({"dispute_status": "open"}, "DISPUTE_OPEN"),
        ({"dispute_status": "upheld"}, "DISPUTE_UPHELD"),
    ],
)
def test_each_missing_link_blocks_billing_with_its_reason(change: dict, reason: str) -> None:  # type: ignore[type-arg]
    ok, reasons = eligibility(facts(**change))
    assert not ok and reason in reasons


def test_rejected_dispute_does_not_block() -> None:
    assert eligibility(facts(dispute_status="rejected"))[0]


@given(
    st.booleans(),
    st.sampled_from(["attended", "no_show", "booked"]),
    st.sampled_from(["calendar", "operator", None]),
    st.sampled_from([True, False, None]),
    st.booleans(),
    st.integers(-1, 50_000),
    st.booleans(),
    st.sampled_from([None, "open", "upheld", "rejected"]),
)
def test_billable_iff_every_rule_holds(bs, status, src, q, billed, fee, signed, dispute) -> None:  # type: ignore[no-untyped-def]
    f = facts(
        booked_by_system=bs,
        meeting_status=status,
        attendance_status=status,
        attendance_source=src,
        qualification_met=q,
        already_billed=billed,
        per_meeting_minor=fee,
        pricing_signed=signed,
        dispute_status=dispute,
    )
    expected = (
        bs
        and status == "attended"
        and src == "calendar"
        and q is True
        and not billed
        and fee > 0
        and signed
        and dispute in (None, "rejected")
    )
    ok, reasons = eligibility(f)
    assert ok == expected and (ok == (reasons == []))


RULES = [
    TaxRuleRow(1, "uk_not_vat_registered", {"supplier_vat_registered": False}, 0, "Not registered for VAT.", False),
    TaxRuleRow(
        2,
        "uk_vat_standard_b2b_uk",
        {"supplier_vat_registered": True, "customer_country": "GB"},
        2000,
        "VAT at 20%.",
        False,
    ),
    TaxRuleRow(
        3,
        "eu_b2b_reverse_charge",
        {"supplier_vat_registered": True, "customer_in_eu": True, "customer_is_business": True},
        0,
        "Reverse charge",
        False,
    ),
]


def test_tax_rule_selection() -> None:
    assert select_rule(RULES, tax_facts(False, "GB")).code == "uk_not_vat_registered"
    assert select_rule(RULES, tax_facts(False, "FR")).code == "uk_not_vat_registered"
    assert select_rule(RULES, tax_facts(True, "GB")).code == "uk_vat_standard_b2b_uk"
    assert select_rule(RULES, tax_facts(True, "IE")).code == "eu_b2b_reverse_charge"
    with pytest.raises(TaxUnresolved) as e:  # VAT-registered, customer in the US: no rule -> block, never guess
        select_rule(RULES, tax_facts(True, "US"))
    assert e.value.code == "NO_TAX_RULE"
    clash = [
        *RULES,
        TaxRuleRow(4, "other_gb", {"supplier_vat_registered": True, "customer_country": "GB"}, 500, "x", False),
    ]
    with pytest.raises(TaxUnresolved) as e:
        select_rule(clash, tax_facts(True, "GB"))
    assert e.value.code == "AMBIGUOUS_TAX_RULE"
    assert "NI" not in tax_facts(True, "GB") and not tax_facts(True, "GB")["customer_in_eu"]


@pytest.mark.parametrize(
    ("sub", "bps", "tax"),
    [
        (105_000, 2000, 21_000),
        (333, 2000, 67),
        (332, 2000, 66),
        (-30_000, 2000, -6_000),
        (-333, 2000, -67),
        (999, 0, 0),
    ],
)
def test_tax_rounding_half_up_and_symmetric(sub: int, bps: int, tax: int) -> None:
    assert tax_minor(sub, bps) == tax


@given(st.integers(-(10**9), 10**9), st.integers(0, 10_000))
def test_tax_is_bounded_and_odd(sub: int, bps: int) -> None:
    t = tax_minor(sub, bps)
    assert abs(t - sub * bps / 10_000) <= 0.5 and tax_minor(-sub, bps) == -t


def test_period_and_money() -> None:
    assert month_bounds(date(2026, 12, 1)) == (date(2026, 12, 1), date(2026, 12, 31))
    assert month_bounds(date(2027, 2, 1))[1] == date(2027, 2, 28)
    with pytest.raises(BillingError):
        month_bounds(date(2026, 10, 2))
    assert money(105_000, "GBP") == "£1,050.00" and money(-30_000, "EUR") == "-€300.00"


def test_terms_hash_is_order_independent_and_change_sensitive() -> None:
    p = Pricing(per_meeting_minor=30_000).model_dump(mode="json")
    assert terms_hash(p) == terms_hash(dict(reversed(list(p.items()))))
    assert terms_hash(p) != terms_hash({**p, "per_meeting_minor": 30_001})
    q = Qualification.model_validate({"criteria": [{"id": "need", "question": "Has a need"}]}).model_dump(mode="json")
    assert terms_hash(q) != terms_hash({**q, "min_confidence": 0.8})
