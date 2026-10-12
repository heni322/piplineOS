from __future__ import annotations

import itertools

import pytest

from app.domain.leads.state_machine import PRE_MEETING, TERMINAL, TRANSITIONS, State, allowed

S = State


def test_happy_path_is_allowed() -> None:
    path = [
        S.DISCOVERED,
        S.ENRICHED,
        S.COMPLIANCE_CHECKED,
        S.PENDING_APPROVAL,
        S.APPROVED,
        S.CONTACTED,
        S.REPLIED,
        S.QUALIFIED,
        S.BOOKED,
        S.CONFIRMED,
        S.ATTENDED,
        S.BILLED,
    ]
    for a, b in itertools.pairwise(path):
        assert allowed(a, b), (a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (S.ENRICHED, S.CONTACTED),  # cannot skip compliance + approval
        (S.DISCOVERED, S.APPROVED),
        (S.PENDING_APPROVAL, S.CONTACTED),  # cannot skip approval
        (S.COMPLIANCE_CHECKED, S.APPROVED),
        (S.QUALIFIED, S.ATTENDED),
        (S.BOOKED, S.BILLED),  # billing needs attendance
        (S.ERASED, S.DISCOVERED),
    ],
)
def test_illegal_shortcuts_are_refused(a: State, b: State) -> None:
    assert not allowed(a, b)


def test_terminal_states_only_lead_to_erased() -> None:
    for t in TERMINAL - {S.ERASED}:
        assert TRANSITIONS[t] == frozenset({S.ERASED})
    assert TRANSITIONS[S.ERASED] == frozenset()


def test_every_non_terminal_state_can_be_suppressed_and_erased() -> None:
    for s in set(State) - TERMINAL:
        assert allowed(s, S.SUPPRESSED, S.CONTACTED) and allowed(s, S.ERASED, S.CONTACTED)


def test_objection_ends_lead_only_before_a_meeting() -> None:
    for s in PRE_MEETING:
        assert allowed(s, S.OBJECTED, S.CONTACTED)
    for s in (S.BOOKED, S.CONFIRMED, S.ATTENDED, S.BILLED):
        assert not allowed(s, S.OBJECTED)


def test_contacted_only_from_approved_or_replied() -> None:
    sources = {s for s, targets in TRANSITIONS.items() if S.CONTACTED in targets}
    assert sources == {S.APPROVED, S.REPLIED}


def test_escalation_resolves_only_to_targets_valid_from_previous_state() -> None:
    assert allowed(S.ESCALATED, S.PENDING_APPROVAL, S.COMPLIANCE_CHECKED)
    assert allowed(S.ESCALATED, S.COMPLIANCE_CHECKED, S.COMPLIANCE_CHECKED)
    assert not allowed(S.ESCALATED, S.CONTACTED, S.COMPLIANCE_CHECKED)
    assert not allowed(S.ESCALATED, S.APPROVED, None)
