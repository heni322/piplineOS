from __future__ import annotations

from typing import Any, Protocol


class CrmAdapter(Protocol):
    name: str

    def upsert_deal(self, external_ref: str, fields: dict[str, Any], idempotency_key: str) -> str: ...
