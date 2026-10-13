"""CRM adapter interface: the client's own CRM receives the meetings PipelineOS books for them (company, contact,
and a deal whose stage follows the meeting), and can tell us when a meeting was attended.

Writing is a side effect: only app.domain.outbound may import CRM adapters (import contract `outbound-only`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class CrmContact:
    email: str
    first_name: str
    last_name: str
    job_title: str | None


@dataclass(frozen=True)
class CrmMeetingRecord:
    """What one meeting looks like in the client's CRM. `contact` is None when personal data must not be sent
    (the person objected or was erased): then only the deal's stage may change."""

    ref: str  # stable short reference, also written into the deal name so a retry can find the deal
    deal_name: str
    stage_id: str
    pipeline_id: str
    starts_at: datetime
    company_name: str | None
    company_domain: str | None
    contact: CrmContact | None


@dataclass
class CrmIds:
    company_id: str | None = None
    contact_id: str | None = None
    deal_id: str | None = None
    created: list[str] = field(default_factory=list)  # which objects this call created (for the audit trail)


class CrmAdapter(Protocol):
    name: str

    def sync(self, record: CrmMeetingRecord, known: CrmIds) -> CrmIds:
        """Create or update company, contact and deal. Idempotent: known ids are reused, and a deal whose name
        carries `record.ref` is found again rather than created twice."""
        ...

    def deal_stage(self, deal_id: str) -> str | None: ...
