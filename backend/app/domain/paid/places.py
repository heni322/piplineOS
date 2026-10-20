"""Google Places behind the paid-call gate and a monthly cap.

- Only with the three keys (Places adapter live, client live, global go-live with the checklist signed), never with
  FREE_ONLY=true, and never in sandbox (Google has no free test key). Otherwise the would-be search is written to the
  dry-run log and nothing is called.
- Every billed request is first counted in `provider_usage` under PLACES_MONTHLY_CAP (default 1,000 = Google's free
  monthly allowance for these fields), atomically across workers. At the cap, searching stops until next month.
- Nothing returned by Places is stored (Google's terms). Websites are handed straight to the website check, which
  keeps a site only when its own pages prove it belongs to the company.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.adapters.companies_house.live import normalise_name
from app.adapters.google_places import MAX_PAGES, GooglePlaces, PlaceHit
from app.core.logging import get_logger
from app.db.session import DbContext, transaction
from app.domain.paid.gate import PaidGate

log = get_logger(__name__)
PROVIDER = "google.places"
MAX_SITES = 3  # websites tried per company: the best few matches by name


class CapReached(Exception):
    pass


def reserve_call(engine: Engine, provider: str, cap: int, now: datetime | None = None) -> int:
    """Count one call under the monthly cap, or raise CapReached. Returns the count including this call."""
    if cap <= 0:
        raise CapReached(provider)
    period = (now or datetime.now(UTC)).strftime("%Y-%m")
    with transaction(DbContext.system(), engine) as conn:
        n = conn.execute(
            text("""INSERT INTO provider_usage (provider, period, calls) VALUES (:p, :m, 1)
                    ON CONFLICT (provider, period) DO UPDATE SET calls = provider_usage.calls + 1, updated_at = now()
                    WHERE provider_usage.calls < :cap
                    RETURNING calls"""),
            {"p": provider, "m": period, "cap": cap},
        ).scalar()
    if n is None:
        raise CapReached(provider)
    return int(n)


def usage(engine: Engine, provider: str = PROVIDER, now: datetime | None = None) -> int:
    period = (now or datetime.now(UTC)).strftime("%Y-%m")
    with transaction(DbContext.system(), engine) as conn:
        n = conn.execute(
            text("SELECT calls FROM provider_usage WHERE provider = :p AND period = :m"), {"p": provider, "m": period}
        ).scalar()
    return int(n or 0)


def _words(name: str) -> set[str]:
    return {w for w in normalise_name(name).split() if len(w) > 1}


class GatedPlaces:
    def __init__(
        self, provider: GooglePlaces, gate: PaidGate, engine: Engine, *, cap: int, region: str, language: str
    ) -> None:
        self.provider, self.gate, self.engine = provider, gate, engine
        self.cap, self.region, self.language = cap, region, language

    @property
    def allowed(self) -> bool:
        return self.gate.mode == "live"  # no sandbox: every Places request is billed

    def _page(self, query: str, operation: str, subject: str, token: str | None) -> tuple[list[PlaceHit], str | None]:
        if not self.allowed:
            self.gate.record(adapter=PROVIDER, operation=operation, subject=subject, lead_id=None)
            return [], None
        try:
            reserve_call(self.engine, PROVIDER, self.cap)
        except CapReached:
            log.warning("places.monthly_cap_reached", cap=self.cap)
            return [], None
        return self.provider.search(query, region_code=self.region, language=self.language, page_token=token)

    def websites_for(self, number: str, company_name: str | None) -> list[str]:
        """Candidate websites for a company (one billed request). The caller must still prove each one."""
        if not company_name:
            return []
        hits, _ = self._page(company_name, "find_website", f"Find the website of {company_name}", None)
        want = _words(company_name)
        ranked = sorted(
            (h for h in hits if h.website),
            key=lambda h: len(want & _words(h.name)) / max(len(want), 1),
            reverse=True,
        )
        return list(dict.fromkeys(h.website for h in ranked if h.website))[:MAX_SITES]

    def discover(self, query: str, pages: int = 1) -> list[PlaceHit]:
        """Businesses matching an activity and area ("IT support Leeds"): one billed request per page of 20."""
        out: list[PlaceHit] = []
        token: str | None = None
        for _ in range(max(1, min(pages, MAX_PAGES))):
            hits, token = self._page(query, "discover", f"Search businesses: {query}", token)
            out += hits
            if not token:
                break
        return out
