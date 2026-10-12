"""Property tests: random walks over the transition table never violate the lifecycle invariants."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.leads.state_machine import TERMINAL, TRANSITIONS, State, allowed

S = State


@settings(max_examples=500)
@given(st.lists(st.integers(min_value=0, max_value=10_000), min_size=1, max_size=40))
def test_random_walks_respect_invariants(choices: list[int]) -> None:
    state, pre, seen_contacted_via = S.DISCOVERED, None, []
    approved_seen = False
    for c in choices:
        options = sorted(
            TRANSITIONS[state]
            if state != S.ESCALATED
            else (TRANSITIONS[pre] | {pre, S.SUPPRESSED, S.ERASED, S.OBJECTED})
            if pre
            else {S.SUPPRESSED, S.ERASED, S.OBJECTED}
        )
        if not options:
            break
        nxt = options[c % len(options)]
        assert allowed(state, nxt, pre)
        if nxt == S.CONTACTED:
            seen_contacted_via.append(state)
            # contacted is only reachable after approval at least once
            assert approved_seen
        if nxt == S.APPROVED:
            approved_seen = True
        if state in TERMINAL:
            assert nxt == S.ERASED
        pre = state if nxt == S.ESCALATED else (pre if state == S.ESCALATED and nxt == S.ESCALATED else None)
        if nxt == S.ESCALATED:
            pre = state
        state = nxt
    if state == S.ERASED:
        assert TRANSITIONS[S.ERASED] == frozenset()
