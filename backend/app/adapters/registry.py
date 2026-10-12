"""Provider selection by adapter mode. Until Phase 6 only mock implementations exist: live/sandbox modes raise
AdapterNotConfigured, so a misconfiguration fails loudly instead of silently calling a real service."""

from __future__ import annotations

from app.adapters.base import AdapterNotConfigured
from app.settings import Settings


def providers_for(settings: Settings):  # type: ignore[no-untyped-def]
    for name in ("signals", "enrichment", "verifier", "companies_house"):
        mode = getattr(settings, f"{name}_adapter_mode")
        if mode != "mock":
            raise AdapterNotConfigured(f"{name} adapter mode {mode!r} is not implemented until Phase 6")
    from app.demo.seed import providers  # mock providers backed by the synthetic demo dataset

    return providers()
