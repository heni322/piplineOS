"""Three-key live switch (ADR-0004). Anything other than all-keys-on resolves to dry_run."""

from __future__ import annotations

from typing import Literal

SendMode = Literal["dry_run", "sandbox", "live"]


def resolve_mode(*, adapter_mode: str, tenant_live: bool, global_go_live: bool, golive_complete: bool) -> SendMode:
    if adapter_mode == "live" and tenant_live and global_go_live and golive_complete:
        return "live"
    if adapter_mode == "sandbox":
        return "sandbox"
    return "dry_run"
