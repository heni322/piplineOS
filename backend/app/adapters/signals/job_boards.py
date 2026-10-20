"""Hiring signals from the public job boards of the companies on a client's watchlist.

Greenhouse, Lever, Ashby, Recruitee, Workable and SmartRecruiters publish each employer's open roles through free,
public, read-only JSON APIs (no key, no account), built for companies to show their own vacancies on their careers
page; the last three are common with French and other European employers. A company opening roles in the area the
client sells into is a timing trigger: new team, new budget, new needs.

What we take from a posting, and nothing more (data minimisation): the role title, team and location, its
publication date and its public URL. Postings name no individual; recruiter names, salaries and the job description
are never read.

A board is written "<ats>:<board name>", e.g. "greenhouse:contoso", "lever:contoso", "lever-eu:contoso" (Lever's EU
instance), "ashby:contoso", "recruitee:contoso" (contoso.recruitee.com), "workable:contoso"
(apply.workable.com/contoso) or "smartrecruiters:Contoso" (the company identifier in its SmartRecruiters links).
The query is the client's hiring keywords separated by ";" (a keyword starting with "-" excludes a role); an empty
query counts every new role.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.adapters.http import ProviderError, check, client
from app.adapters.signals.base import RawSignal
from app.adapters.signals.ocds import _matches, _parse_dt, parse_query

MAX_TITLES = 8


@dataclass(frozen=True)
class Posting:
    title: str
    team: str
    location: str
    published: datetime
    url: str
    ref: str


@dataclass(frozen=True)
class WatchedBoard:
    company_name: str
    board: str  # "<ats>:<board name>"
    domain: str | None = None
    company_number: str | None = None
    country_hint: str | None = None


def _ms(value: Any) -> datetime | None:
    if isinstance(value, int | float) and value > 0:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    return None


def _greenhouse(data: Any) -> list[Posting]:
    out = []
    for j in (data or {}).get("jobs") or []:
        when = _parse_dt(j.get("first_published")) or _parse_dt(j.get("updated_at"))
        if not when or not j.get("title"):
            continue
        teams = ", ".join(str(d.get("name")) for d in j.get("departments") or [] if d.get("name"))
        out.append(
            Posting(
                title=str(j["title"]),
                team=teams,
                location=str((j.get("location") or {}).get("name") or ""),
                published=when,
                url=str(j.get("absolute_url") or ""),
                ref=str(j.get("id")),
            )
        )
    return out


def _lever(data: Any) -> list[Posting]:
    out = []
    for j in data if isinstance(data, list) else []:
        when = _ms(j.get("createdAt"))
        if not when or not j.get("text"):
            continue
        cat = j.get("categories") or {}
        out.append(
            Posting(
                title=str(j["text"]),
                team=str(cat.get("department") or cat.get("team") or ""),
                location=str(cat.get("location") or ""),
                published=when,
                url=str(j.get("hostedUrl") or ""),
                ref=str(j.get("id")),
            )
        )
    return out


def _ashby(data: Any) -> list[Posting]:
    out = []
    for j in (data or {}).get("jobs") or []:
        when = _parse_dt(j.get("publishedAt"))
        if not when or not j.get("title") or j.get("isListed") is False:
            continue
        out.append(
            Posting(
                title=str(j["title"]),
                team=str(j.get("department") or j.get("team") or ""),
                location=str(j.get("location") or ""),
                published=when,
                url=str(j.get("jobUrl") or ""),
                ref=str(j.get("id")),
            )
        )
    return out


def _dt(value: Any) -> datetime | None:
    """ISO dates, plus Recruitee's "2026-10-15 09:31:36 UTC"."""
    return _parse_dt(value.replace(" UTC", "+00:00")) if isinstance(value, str) else None


def _recruitee(data: Any) -> list[Posting]:
    out = []
    for j in (data or {}).get("offers") or []:
        when = _dt(j.get("published_at"))
        if not when or not j.get("title") or j.get("status", "published") != "published":
            continue
        out.append(
            Posting(
                title=str(j["title"]),
                team=str(j.get("department") or ""),
                location=str(j.get("location") or j.get("city") or ""),
                published=when,
                url=str(j.get("careers_url") or ""),
                ref=str(j.get("id")),
            )
        )
    return out


def _workable(data: Any) -> list[Posting]:
    out = []
    for j in (data or {}).get("jobs") or []:
        when = _dt(j.get("published_on")) or _dt(j.get("created_at"))
        if not when or not j.get("title"):
            continue
        place = ", ".join(str(x) for x in (j.get("city"), j.get("country")) if x)
        out.append(
            Posting(
                title=str(j["title"]),
                team=str(j.get("department") or ""),
                location=place,
                published=when,
                url=str(j.get("url") or ""),
                ref=str(j.get("shortcode")),
            )
        )
    return out


def _smartrecruiters(data: Any) -> list[Posting]:
    out = []
    for j in (data or {}).get("content") or []:
        when = _dt(j.get("releasedDate"))
        if not when or not j.get("name") or j.get("visibility", "PUBLIC") != "PUBLIC":
            continue
        company = str((j.get("company") or {}).get("identifier") or "")
        out.append(
            Posting(
                title=str(j["name"]),
                team=str((j.get("department") or {}).get("label") or (j.get("function") or {}).get("label") or ""),
                location=str((j.get("location") or {}).get("fullLocation") or ""),
                published=when,
                url=f"https://jobs.smartrecruiters.com/{company}/{j.get('id')}" if company and j.get("id") else "",
                ref=str(j.get("id")),
            )
        )
    return out


# ats -> (API base URL, path for a board, parser, public board page). A path may be a full URL when the ATS gives
# each employer its own sub-domain (Recruitee).
ATS: dict[str, tuple[str, str, Callable[[Any], list[Posting]], str]] = {
    "greenhouse": (
        "https://boards-api.greenhouse.io",
        "/v1/boards/{b}/jobs",
        _greenhouse,
        "https://job-boards.greenhouse.io/{b}",
    ),
    "lever": ("https://api.lever.co", "/v0/postings/{b}?mode=json", _lever, "https://jobs.lever.co/{b}"),
    "lever-eu": ("https://api.eu.lever.co", "/v0/postings/{b}?mode=json", _lever, "https://jobs.eu.lever.co/{b}"),
    "ashby": ("https://api.ashbyhq.com", "/posting-api/job-board/{b}", _ashby, "https://jobs.ashbyhq.com/{b}"),
    # checked live on 2026-10-19 (public endpoints built for careers pages, no key)
    "recruitee": (
        "https://recruitee.com",
        "https://{b}.recruitee.com/api/offers/",
        _recruitee,
        "https://{b}.recruitee.com",
    ),
    "workable": (
        "https://apply.workable.com",
        "/api/v1/widget/accounts/{b}",
        _workable,
        "https://apply.workable.com/{b}/",
    ),
    "smartrecruiters": (
        "https://api.smartrecruiters.com",
        "/v1/companies/{b}/postings?limit=100",
        _smartrecruiters,
        "https://jobs.smartrecruiters.com/{b}",
    ),
}
SUBDOMAIN = {"recruitee"}  # the board name becomes a host name: letters, digits and hyphens only


def parse_board(board: str) -> tuple[str, str] | None:
    """ "greenhouse:contoso" -> ("greenhouse", "contoso"); None for an unknown ATS or an unsafe board name."""
    ats, _, name = board.strip().partition(":")
    ats, name = ats.lower(), name.strip()
    if ats not in ATS or not name or not all(c.isalnum() or c in "-_." for c in name) or name.startswith("."):
        return None
    if ats in SUBDOMAIN and not all(c.isascii() and (c.isalnum() or c == "-") for c in name):
        return None
    return ats, name


class JobBoardSource:
    """New roles on the watched companies' public job boards: one signal per company per run, listing the roles
    published since `since` that match the hiring keywords. A board that fails is skipped; the others still run."""

    name = "job_boards.live"

    def __init__(
        self,
        boards: list[WatchedBoard],
        *,
        transports: dict[str, httpx.BaseTransport] | None = None,
    ) -> None:
        self._boards = boards
        self._clients = {
            ats: client(base, (transports or {}).get(ats), headers={"Accept": "application/json"})
            for ats, (base, *_rest) in ATS.items()
        }
        self.failed: list[str] = []

    def fetch(self, query: str, since: datetime) -> list[RawSignal]:
        include, exclude = parse_query(query)
        out: list[RawSignal] = []
        for w in self._boards:
            parsed = parse_board(w.board)
            if parsed is None:
                self.failed.append(w.board)
                continue
            ats, board = parsed
            _base, path, parser, page = ATS[ats]
            try:
                postings = parser(check(self._clients[ats].get(path.format(b=board))).json())
            except (ProviderError, httpx.HTTPError, ValueError):
                self.failed.append(w.board)  # a removed or renamed board must not stop the others
                continue
            new = [
                p
                for p in postings
                if p.published >= since
                and (not include or _matches(f"{p.title}\n{p.team}", include))
                and not (exclude and _matches(f"{p.title}\n{p.team}", exclude))
            ]
            if new:
                out.append(self._signal(w, ats, board, page.format(b=board), new))
        return out

    @staticmethod
    def _signal(w: WatchedBoard, ats: str, board: str, page: str, new: list[Posting]) -> RawSignal:
        new = sorted(new, key=lambda p: p.published, reverse=True)
        roles = "; ".join(
            p.title + (f" ({p.team})" if p.team else "") + (f", {p.location}" if p.location else "")
            for p in new[:MAX_TITLES]
        )
        more = f" and {len(new) - MAX_TITLES} more" if len(new) > MAX_TITLES else ""
        refs = hashlib.sha256(",".join(sorted(p.ref for p in new)).encode()).hexdigest()[:16]
        return RawSignal(
            company_name=w.company_name,
            domain=w.domain,
            country_hint=w.country_hint or (new[0].location or None),
            signal_type="job_posting",
            title=f"{w.company_name} is hiring: {len(new)} new role{'s' if len(new) > 1 else ''}"[:200],
            text=f"{w.company_name} published {len(new)} new role(s) on its careers page: {roles}{more}."[:1500],
            source_url=new[0].url if len(new) == 1 and new[0].url else page,
            detected_at=new[0].published,
            source_record_id=f"{ats}:{board}:{refs}",
            company_number=w.company_number,
        )
