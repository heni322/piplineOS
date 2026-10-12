"""Agent 3 eval thresholds enforced in CI (brief: TESTING REQUIREMENTS / LLM evaluation suite)."""

from __future__ import annotations

from tests.evals.writer_eval import run_part_a, run_part_b


def test_part_a_pipeline_guarantees_on_40_bundles() -> None:
    r = run_part_a()
    assert r.n == 40
    assert r.unsourced_claims == 0
    assert r.footer_missing == 0
    assert r.over_90_words == 0
    assert r.wrong_language == 0
    assert r.us_spellings == 0
    assert r.gate_failures == 0


def test_part_b_guard_catches_every_flawed_draft_without_false_positives() -> None:
    tp, fn, tn, fp = run_part_b()
    assert tp + fn >= 30 and tn + fp >= 10
    assert fn == 0, "a flawed draft passed the gate"
    assert fp == 0, "a clean draft was blocked"
