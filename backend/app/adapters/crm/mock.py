from __future__ import annotations

from typing import Any


class MockCrm:
    name = "crm.mock"

    def __init__(self) -> None:
        self.deals: dict[str, dict[str, Any]] = {}

    def upsert_deal(self, external_ref: str, fields: dict[str, Any], idempotency_key: str) -> str:
        self.deals[external_ref] = fields
        return external_ref
