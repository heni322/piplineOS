"""Hunter.io (EU-based B2B email data from public web sources): finds the decision-maker at a company and verifies
their work email. Every call costs credits, so it is a PAID call: only app.domain.paid builds these objects, behind
the three-key switch (import contract `paid-only`).

Data minimisation: we take name, job title, work email, country/region and Hunter's confidence. We never take social
profile handles or phone numbers. A person who asked Hunter to stop processing their data (HTTP 451) is never
returned; for a verification that answers 451 we raise PersonObjected so the caller suppresses that address.

API: https://hunter.io/api-documentation/v2 (key in the X-API-KEY header, never in the URL).
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.adapters.base import RateLimited, RetryableProviderError
from app.adapters.enrichment.base import EnrichedPerson
from app.adapters.http import ProviderError, client
from app.adapters.verifier.base import PersonObjected, Verification

BASE_URL = "https://api.hunter.io"
SANDBOX_KEY = "test-api-key"  # Hunter's documented test key: dummy data, no credits
DOMAIN_SEARCH_LIMIT = 10  # Hunter charges 1 credit per 1-10 emails returned: one credit per company
MAX_LOCATED = 3  # at most this many people get a location lookup (0.2 credits each, charged only if found)

VERIFIER_STATUS: dict[str, Verification] = {
    "valid": "valid",
    "invalid": "invalid",
    "disposable": "invalid",
    "accept_all": "risky",
    "webmail": "risky",
    "unknown": "unknown",
}


class _ObjectedAtSource(Exception):
    pass


class CreditsExhausted(ProviderError):
    """Hunter 429: the plan's monthly credits are used up. Not retried until someone tops up."""


def _check(resp: httpx.Response) -> httpx.Response:
    # Hunter uses 403 for its per-second rate limit and 429 for "usage limit reached" (credits).
    if resp.status_code == 403:
        raise RateLimited(resp.headers.get("Retry-After", "hunter rate limit"))
    if resp.status_code == 429:
        raise CreditsExhausted(429, "Hunter credits exhausted")
    if resp.status_code >= 500 or resp.status_code == 222:
        raise RetryableProviderError(f"{resp.status_code} from Hunter")
    if resp.status_code >= 400 and resp.status_code != 451:
        raise ProviderError(resp.status_code, resp.text)
    return resp


def _word(title: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase.lower())}\b", title.lower()) is not None


class _Hunter:
    def __init__(self, api_key: str, *, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise ValueError("Hunter API key missing")
        self._http = client(base_url, transport, headers={"X-API-KEY": api_key})

    def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        return _check(self._http.get(path, params=params))


class HunterEnrichment(_Hunter):
    name = "hunter.enrichment"

    def find_people(
        self,
        company_domain: str,
        persona_titles: list[str],
        company_name: str | None = None,
        *,
        company_number: str | None = None,
    ) -> list[EnrichedPerson]:
        params: dict[str, Any] = {"type": "personal", "limit": DOMAIN_SEARCH_LIMIT}
        if company_domain:
            params["domain"] = company_domain
        elif company_name:
            params["company"] = company_name
        else:
            return []
        resp = self._get("/v2/domain-search", params)
        if resp.status_code == 451:
            return []
        data = resp.json().get("data") or {}
        domain = data.get("domain")
        if not domain:
            return []
        wanted = [t for t in persona_titles if t.strip()]
        candidates = [
            e
            for e in data.get("emails") or []
            if e.get("value")
            and e.get("first_name")
            and e.get("last_name")
            and e.get("position")
            and (any(_word(e["position"], t) for t in wanted) or e.get("seniority") == "executive")
        ]
        candidates.sort(key=lambda e: int(e.get("confidence") or 0), reverse=True)
        out = []
        for e in candidates[:MAX_LOCATED]:
            try:
                located = self._location(e["value"])
            except _ObjectedAtSource:
                continue  # 451: the person objected at source; never returned
            sources = len(e.get("sources") or [])
            out.append(
                EnrichedPerson(
                    person_name=f"{e['first_name']} {e['last_name']}".strip(),
                    title=str(e["position"]),
                    company_domain=str(domain),
                    country=located[0] if located else None,
                    location_text=located[1] if located else None,
                    professional_email=str(e["value"]),
                    profile_url=None,  # never stored, even when the provider has one
                    data_source=f"hunter.io domain-search {domain} ({sources} public source(s))",
                    confidence=max(0.0, min(1.0, int(e.get("confidence") or 0) / 100)),
                )
            )
        return out

    def _location(self, email: str) -> tuple[str | None, str | None] | None:
        """(country code, location text), or None when unknown. Raises _ObjectedAtSource on 451."""
        resp = self._http.get("/v2/people/find", params={"email": email})
        if resp.status_code == 404:
            return None
        if resp.status_code == 451:
            raise _ObjectedAtSource
        data = (_check(resp).json().get("data")) or {}
        geo = data.get("geo") or {}
        text = data.get("location") or ", ".join(
            str(x) for x in (geo.get("city"), geo.get("state"), geo.get("country")) if x
        )
        code = geo.get("countryCode")
        if not text and not code:
            return None
        country = str(code).upper() if code else None
        return (country, str(text) if text else country)


class HunterVerifier(_Hunter):
    name = "hunter.verifier"

    def verify(self, email: str) -> Verification:
        resp = self._get("/v2/email-verifier", {"email": email})
        if resp.status_code == 451:
            raise PersonObjected(email)
        if resp.status_code == 202:  # still checking: not an answer. The lead waits and is verified on a later run
            raise RetryableProviderError("verification still in progress")
        status = str((resp.json().get("data") or {}).get("status") or "unknown")
        return VERIFIER_STATUS.get(status, "unknown")
