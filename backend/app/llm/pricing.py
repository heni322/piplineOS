"""Cost from config/llm_prices.yaml. Unknown/unset price or FX rate -> None (stored as NULL; never invented).

Supports USD list prices with an owner-chosen FX rate (fx_usd_gbp) and, for compatibility, direct GBP micros."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import yaml


def load_pricer(path: Path) -> Callable[[str, int, int], int | None]:
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}) or {}
    models = data.get("models", {}) or {}
    fx = data.get("fx_usd_gbp")

    def per_mtok_micros(p: dict[str, object], side: str) -> int | None:
        gbp = p.get(f"{side}_per_mtok_micros_gbp")
        if gbp is not None:
            return int(str(gbp))
        usd = p.get(f"{side}_per_mtok_usd")
        if usd is None or fx is None:
            return None
        return int(Decimal(str(usd)) * Decimal(str(fx)) * 1_000_000)

    def price(model: str, tokens_in: int, tokens_out: int) -> int | None:
        if model.startswith("local:"):  # a free model on this machine
            return 0
        p = models.get(model) or {}
        pin, pout = per_mtok_micros(p, "input"), per_mtok_micros(p, "output")
        if pin is None or pout is None:
            return None
        return (tokens_in * pin + tokens_out * pout) // 1_000_000

    return price
