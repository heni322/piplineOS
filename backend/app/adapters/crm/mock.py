from __future__ import annotations

import uuid

from app.adapters.crm.base import CrmIds, CrmMeetingRecord


class MockCrm:
    """In-memory CRM for tests and demos."""

    name = "crm.mock"

    def __init__(self) -> None:
        self.deals: dict[str, dict[str, object]] = {}
        self.calls: list[str] = []

    def sync(self, record: CrmMeetingRecord, known: CrmIds) -> CrmIds:
        self.calls.append(record.ref)
        out = CrmIds(known.company_id, known.contact_id, known.deal_id)
        if out.deal_id is None:
            out.deal_id = next((k for k, d in self.deals.items() if record.ref in str(d["name"])), None)
        if out.deal_id is None:
            out.deal_id = str(uuid.uuid4())
            out.created.append("deal")
        self.deals[out.deal_id] = {"name": record.deal_name, "stage": record.stage_id, "contact": record.contact}
        return out

    def deal_stage(self, deal_id: str) -> str | None:
        d = self.deals.get(deal_id)
        return str(d["stage"]) if d else None
