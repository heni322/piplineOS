"""AI cost: official USD list prices x an owner-chosen FX rate; no rate -> no cost (never invented)."""

from __future__ import annotations

from pathlib import Path

from app.config_paths import config_root
from app.llm.pricing import load_pricer


def test_shipped_prices_have_no_fx_rate_so_costs_stay_unknown() -> None:
    price = load_pricer(config_root() / "llm_prices.yaml")
    assert price("claude-sonnet-5-5", 2000, 300) is None


def test_cost_with_an_fx_rate(tmp_path: Path) -> None:
    f = tmp_path / "p.yaml"
    f.write_text(
        "fx_usd_gbp: 0.75\nmodels:\n  claude-sonnet-5-5: {input_per_mtok_usd: 2.00, output_per_mtok_usd: 10.00}\n"
        "  legacy: {input_per_mtok_micros_gbp: 1000000, output_per_mtok_micros_gbp: 2000000}\n"
    )
    price = load_pricer(f)
    # 2,000 in x $2/M + 300 out x $10/M = $0.007 -> x0.75 = £0.00525 = 5,250 micros
    assert price("claude-sonnet-5-5", 2000, 300) == 5250
    assert price("legacy", 1_000_000, 500_000) == 2_000_000
    assert price("unknown-model", 10, 10) is None
