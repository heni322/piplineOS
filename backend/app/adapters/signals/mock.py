"""Synthetic signal source (fictional companies, .example domains). Realistic datasets arrive in Phase 3."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.adapters.signals.base import RawSignal


class MockSignalSource:
    name = "signals.mock"

    def __init__(self, records: list[RawSignal] | None = None) -> None:
        now = datetime.now(UTC)
        self.records = (
            records
            if records is not None
            else [
                RawSignal(
                    "Northwind Managed IT Ltd",
                    "northwind-it.example",
                    "GB",
                    "job_posting",
                    "Hiring: Head of Service Delivery",
                    "We are hiring a Head of Service Delivery in Leeds.",
                    "https://jobs.example/northwind/123",
                    now - timedelta(days=1),
                    "mock-1",
                ),
                RawSignal(
                    "Fabrikam Cloud Partners Ltd",
                    "fabrikam-cloud.example",
                    "GB",
                    "tender",
                    "Awarded: council IT support framework",
                    "Contract award notice (synthetic).",
                    "https://tenders.example/notice/456",
                    now - timedelta(days=2),
                    "mock-2",
                ),
            ]
        )

    def fetch(self, query: str, since: datetime) -> list[RawSignal]:
        return [r for r in self.records if r.detected_at >= since]
