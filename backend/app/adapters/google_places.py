"""Google Places API (New), Text Search: companies by name or by activity and area, with their website.

Paid (Google bills it; the first 1,000 "Enterprise" searches a month are free, then $35 per 1,000, list price read
2026-10-19), so only app.domain.paid builds this (import contract `paid-only`), behind the three-key switch and a
monthly cap.

Google's terms allow storing only the place ID. So nothing from Places is stored: the website is used at once to
visit the company's own site, which must then prove it belongs to the company (its register number on its pages),
and only what that site itself publishes is kept. Phone numbers, reviews and opening hours are not requested.

API: POST https://places.googleapis.com/v1/places:searchText, key in the X-Goog-Api-Key header (never the URL),
response fields chosen with X-Goog-FieldMask (20 results a page, at most 60 a query).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.adapters.base import RateLimited, RetryableProviderError
from app.adapters.http import ProviderError, client

BASE_URL = "https://places.googleapis.com"
FIELDS = "places.id,places.displayName,places.formattedAddress,places.websiteUri,places.businessStatus,nextPageToken"
PAGE_SIZE = 20
MAX_PAGES = 3  # Google returns at most 60 results a query


@dataclass(frozen=True)
class PlaceHit:
    place_id: str
    name: str
    address: str
    website: str | None  # bare host, "www." removed; None when the place lists no website


def website_domain(uri: str | None) -> str | None:
    host = (urlsplit(uri or "").hostname or "").lower().removeprefix("www.")
    return host if "." in host else None


def _check(resp: httpx.Response) -> httpx.Response:
    if resp.status_code == 429:
        raise RateLimited(resp.headers.get("Retry-After", "places rate limit"))
    if resp.status_code >= 500:
        raise RetryableProviderError(f"{resp.status_code} from Google Places")
    if resp.status_code >= 400:
        raise ProviderError(resp.status_code, resp.text[:300])
    return resp


class GooglePlaces:
    name = "google.places"

    def __init__(self, api_key: str, *, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise ValueError("Google Places API key missing")
        self._http = client(base_url, transport, headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELDS})

    def search(
        self, query: str, *, region_code: str, language: str = "en", page_token: str | None = None
    ) -> tuple[list[PlaceHit], str | None]:
        """One page (one billed request). Permanently closed places are left out."""
        body: dict[str, object] = {
            "textQuery": query[:300],
            "regionCode": region_code.lower(),
            "languageCode": language,
            "pageSize": PAGE_SIZE,
        }
        if page_token:
            body["pageToken"] = page_token
        data = _check(self._http.post("/v1/places:searchText", json=body)).json()
        hits = [
            PlaceHit(
                place_id=str(p.get("id", "")),
                name=str((p.get("displayName") or {}).get("text", "")),
                address=str(p.get("formattedAddress", "")),
                website=website_domain(p.get("websiteUri")),
            )
            for p in data.get("places", []) or []
            if p.get("businessStatus") != "CLOSED_PERMANENTLY"
        ]
        return hits, data.get("nextPageToken") or None
