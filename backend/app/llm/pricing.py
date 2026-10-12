"""Cost from config/llm_prices.yaml. Unknown/unset price -> None (stored as NULL; never invented)."""

from __future__ import annotations

from pathlib import Path

import yaml


def load_pricer(path: Path):  # type: ignore[no-untyped-def]
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    models = (data or {}).get("models", {})

    def price(model: str, tokens_in: int, tokens_out: int) -> int | None:
        p = models.get(model) or {}
        pin, pout = p.get("input_per_mtok_micros_gbp"), p.get("output_per_mtok_micros_gbp")
        if pin is None or pout is None:
            return None
        return (tokens_in * int(pin) + tokens_out * int(pout)) // 1_000_000

    return price
