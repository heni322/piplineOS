"""UK public procurement award notices as trigger signals: Contracts Finder and Find a Tender.

Both publish Open Contracting Data Standard (OCDS) release packages through free, public, read-only APIs (no key).
A supplier winning a public contract is a growth trigger: new work, new capacity needs.

What we take from a notice, and nothing more (data minimisation):
- the supplier's organisation name, its Companies House number when the notice gives one, and its country/region
- the contract title, description, value and buyer, as UNTRUSTED text for scoring

Contact names, emails and phone numbers in the notice (`contactPoint`) are never read.

The query is the tenant's ICP keywords separated by ";". A keyword starting with "-" excludes a notice.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.adapters.http import check, client
from app.adapters.signals.base import RawSignal

CONTRACTS_FINDER = "https://www.contractsfinder.service.gov.uk"
FIND_A_TENDER = "https://www.find-tender.service.gov.uk"
MAX_TEXT = 1500


def parse_query(query: str) -> tuple[list[str], list[str]]:
    words = [w.strip().lower() for w in query.split(";") if w.strip()]
    return [w for w in words if not w.startswith("-")], [
        w[1:].strip() for w in words if w.startswith("-") and w[1:].strip()
    ]


def _matches(text: str, phrases: list[str]) -> bool:
    low = text.lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", low) for p in phrases)


def normalise_company_number(value: str) -> str | None:
    """Companies House numbers are 8 characters: digits, or a 2-letter prefix (SC, NI, OC...) and 6 digits."""
    v = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    if v.isdigit() and 1 <= len(v) <= 8:
        return v.zfill(8)
    if re.fullmatch(r"[A-Z]{2}\d{1,6}", v):
        return v[:2] + v[2:].zfill(6)
    return None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _money(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("amount"), int | float):
        return f"{value.get('currency') or ''} {value['amount']:,.0f}".strip()
    return ""


class OcdsAwardSource:
    """Award notices from one OCDS publisher. `fetch` follows the publisher's own next-page links, on the same host
    only, up to `max_pages` pages."""

    def __init__(
        self,
        name: str,
        base_url: str,
        path: str,
        since_param: str,
        notice_url: Callable[[dict[str, Any]], str],
        *,
        transport: httpx.BaseTransport | None = None,
        page_size: int = 100,
        max_pages: int = 10,
    ) -> None:
        self.name = name
        self._base = base_url
        self._path = path
        self._since_param = since_param
        self._notice_url = notice_url
        self._page_size = page_size
        self._max_pages = max_pages
        self._http = client(base_url, transport, headers={"Accept": "application/json"})

    def _pages(self, since: datetime) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] | None = {
            self._since_param: since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            "stages": "award",
            "limit": self._page_size,
        }
        url = self._path
        host = urlsplit(self._base).netloc
        for _ in range(self._max_pages):
            page = check(self._http.get(url, params=params)).json()
            yield page
            nxt = (page.get("links") or {}).get("next")
            if not nxt or not page.get("releases"):
                return
            parts = urlsplit(nxt)
            if parts.netloc and parts.netloc != host:  # never follow a link to another site
                return
            url, params = (parts.path + ("?" + parts.query if parts.query else "")), None

    def fetch(self, query: str, since: datetime) -> list[RawSignal]:
        include, exclude = parse_query(query)
        out: list[RawSignal] = []
        seen: set[str] = set()
        for page in self._pages(since):
            for rel in page.get("releases") or []:
                for sig in self._signals(rel, include, exclude):
                    if sig.source_record_id not in seen:
                        seen.add(sig.source_record_id)
                        out.append(sig)
        return out

    def _signals(self, rel: dict[str, Any], include: list[str], exclude: list[str]) -> list[RawSignal]:
        if "award" not in (rel.get("tag") or []):
            return []
        tender = rel.get("tender") or {}
        title = str(tender.get("title") or "").strip()
        description = str(tender.get("description") or "").strip()
        classes = [tender.get("classification") or {}] + [
            i.get("classification") or {} for i in tender.get("items") or []
        ]
        cpv = "; ".join(str(c.get("description")) for c in classes if c.get("description"))
        haystack = f"{title}\n{description}\n{cpv}"
        if not include or not _matches(haystack, include) or (exclude and _matches(haystack, exclude)):
            return []

        parties = {str(p.get("id")): p for p in rel.get("parties") or [] if isinstance(p, dict) and p.get("id")}
        supplier_ids = [p_id for p_id, p in parties.items() if "supplier" in (p.get("roles") or [])]
        for award in rel.get("awards") or []:
            for sup in award.get("suppliers") or []:
                if sup.get("id") and str(sup["id"]) not in supplier_ids:
                    supplier_ids.append(str(sup["id"]))
        awards = rel.get("awards") or []
        when = (
            _parse_dt(next((a.get("datePublished") for a in awards if a.get("datePublished")), None))
            or _parse_dt(rel.get("date"))
            or datetime.now(UTC)
        )
        value = next((_money(a.get("value")) for a in awards if _money(a.get("value"))), "") or _money(
            tender.get("value")
        )
        buyer = str((rel.get("buyer") or {}).get("name") or "a public body")
        url = self._notice_url(rel)

        out = []
        for sid in supplier_ids:
            party = parties.get(sid) or next(
                (
                    {"name": s.get("name"), "id": sid}
                    for a in awards
                    for s in a.get("suppliers") or []
                    if s.get("id") == sid
                ),
                None,
            )
            if not party:
                continue
            ident = party.get("identifier") or {}
            name = str(ident.get("legalName") or party.get("name") or "").strip()
            if not name:
                continue
            number = normalise_company_number(str(ident.get("id") or "")) if ident.get("scheme") == "GB-COH" else None
            addr = party.get("address") or {}
            country = addr.get("countryName") or addr.get("region") or None
            text = (
                f"{buyer} awarded the contract '{title}' to {name}"
                + (f" (value {value})" if value else "")
                + f". {description}"
            )[:MAX_TEXT]
            out.append(
                RawSignal(
                    company_name=name,
                    domain=None,  # notices don't give one; resolved later by the enrichment provider
                    country_hint=str(country) if country else None,
                    signal_type="tender",
                    title=f"Awarded: {title}"[:200],
                    text=text,
                    source_url=url,
                    detected_at=when,
                    source_record_id=f"{rel.get('ocid')}:{sid}",
                    company_number=number,
                )
            )
        return out


def _cf_notice(rel: dict[str, Any]) -> str:
    ocid = str(rel.get("ocid") or "")
    guid = ocid.removeprefix("ocds-b5fd17-")
    return f"{CONTRACTS_FINDER}/Notice/{guid}"


def _fts_notice(rel: dict[str, Any]) -> str:
    return f"{FIND_A_TENDER}/Notice/{rel.get('id')}"


def contracts_finder(transport: httpx.BaseTransport | None = None, **kw: Any) -> OcdsAwardSource:
    return OcdsAwardSource(
        "contracts_finder.live",
        CONTRACTS_FINDER,
        "/Published/Notices/OCDS/Search",
        "publishedFrom",
        _cf_notice,
        transport=transport,
        **kw,
    )


def find_a_tender(transport: httpx.BaseTransport | None = None, **kw: Any) -> OcdsAwardSource:
    return OcdsAwardSource(
        "find_a_tender.live",
        FIND_A_TENDER,
        "/api/1.0/ocdsReleasePackages",
        "updatedFrom",
        _fts_notice,
        transport=transport,
        **kw,
    )
