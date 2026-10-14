"""Funding signals for one client from the market-wide filing ingest (app.workflows.register_ingest): active UK
companies in the client's SIC codes that filed a new share allotment (SH01) or a new charge (MR01). No watchlist
needed: this is how the free stack discovers companies it has never seen."""

from __future__ import annotations

from datetime import UTC, datetime, time

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.adapters.signals.base import RawSignal
from app.db.session import DbContext, transaction

FILINGS_PAGE = "https://find-and-update.company-information.service.gov.uk/company/{n}/filing-history"
MAX_PER_RUN = 500
WHAT = {
    "SH01": ("issued new shares", "usually a sign of a funding round"),
    "MR01": ("registered a new charge", "new secured borrowing, often to fund growth"),
}


class RegisterFilingSource:
    name = "companies_house.filings"

    def __init__(self, engine: Engine, sic_codes: list[str]) -> None:
        self._engine = engine
        self._sic = [c for c in sic_codes if c.isdigit()]

    def fetch(self, query: str, since: datetime) -> list[RawSignal]:
        if not self._sic:
            return []
        with transaction(DbContext.system(), self._engine) as conn:
            rows = conn.execute(
                text("""SELECT company_number, company_name, filing_type, filed_on, capital, transaction_ref
                  FROM register_filing_events
                  WHERE sic_codes && CAST(:sic AS text[]) AND filed_on >= :since AND company_status = 'active'
                  ORDER BY filed_on, id LIMIT :lim"""),
                {"sic": self._sic, "since": since.astimezone(UTC).date(), "lim": MAX_PER_RUN},
            ).all()
        out = []
        for r in rows:
            did, meaning = WHAT[r.filing_type]
            amount = f" (capital stated: {r.capital})" if r.capital else ""
            out.append(
                RawSignal(
                    company_name=r.company_name,
                    domain=None,
                    country_hint="GB",
                    signal_type="funding",
                    title=f"{r.company_name} {did}"[:200],
                    text=(
                        f"According to the Companies House register, {r.company_name} {did} on "
                        f"{r.filed_on.isoformat()} (form {r.filing_type}){amount}: {meaning}."
                    ),
                    source_url=FILINGS_PAGE.format(n=r.company_number),
                    detected_at=datetime.combine(r.filed_on, time(9, 0), tzinfo=UTC),
                    source_record_id=f"ch-filing:{r.company_number}:{r.transaction_ref}",
                    company_number=r.company_number,
                )
            )
        return out
