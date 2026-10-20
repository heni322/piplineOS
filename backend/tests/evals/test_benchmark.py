"""The model benchmark runs end to end (here on the simulator, no model needed), only recommends fully safe models,
and reports a model that fails instead of crashing."""

from __future__ import annotations

from app.evals import benchmark, drafts
from app.llm.simulator import SimulatedBackend


class Broken:
    def complete(self, **_kw):  # type: ignore[no-untyped-def]
        raise ConnectionError("model not pulled")


def test_benchmark_ranks_safe_models_and_survives_a_broken_one() -> None:
    results, best = benchmark.run(
        ["sim", "broken"],
        lambda m: SimulatedBackend() if m == "sim" else Broken(),  # type: ignore[return-value]
        ["en", "stage4"],
        with_drafts=True,
    )
    sim, broken = results
    assert set(sim.reply_accuracy) == {"en", "stage4"} and sim.safety_ok and sim.score is not None
    assert sim.drafts_passed.startswith(f"{len(drafts.CASES)}/") or sim.detail["drafts"]["failures"] is not None
    assert broken.score is None and broken.error and "model not pulled" in broken.error
    assert best == "sim"


def test_unsafe_models_are_never_recommended(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    real = benchmark.replies.run

    def unsafe(backend, dataset):  # type: ignore[no-untyped-def]
        r = real(backend, dataset=dataset)
        r.injection_unsafe_action += 1
        return r

    monkeypatch.setattr(benchmark.replies, "run", unsafe)
    results, best = benchmark.run(["sim"], lambda _m: SimulatedBackend(), ["en"], with_drafts=False)
    assert not results[0].safety_ok and results[0].score is None and best is None
