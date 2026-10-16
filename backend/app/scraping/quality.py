"""Data quality for scraped job ads (ADR-0020): only clean, recent, minimal, deduplicated records go further.

Each JobPosting found is either ACCEPTED as a `JobAd` or REJECTED with one counted reason, so a source that suddenly
yields nothing shows WHY (all expired? another employer's ads? broken JSON?) instead of silently going quiet.

Data minimisation (UK GDPR art. 5(1)(c)): an allow-list of fields is read: title, location, dates, employment
type, the ad's URL and the employer's name (to check it IS the company). Descriptions, salaries, contact points,
recruiter names, e-mail addresses and phone numbers are never read, let alone stored.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from app.adapters.companies_house.live import normalise_name
from app.scraping.extract import ATS_LINK, of_type
from app.scraping.fetcher import on_site

EXTRACTOR_VERSION = "jobposting-jsonld/1"
MAX_TITLE = 150
MAX_FUTURE = timedelta(days=2)  # clock skew and time zones; anything later is a data error
MAX_AGE = timedelta(days=180)  # an ad "posted" longer ago than this is stale, whatever validThrough says
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
_EMAILISH = re.compile(r"[^\s@]+@[^\s@]+\.[a-z]{2,}", re.IGNORECASE)
_PHONEISH = re.compile(r"\+?\d[\d\s().-]{7,}\d")
REMOTE = {"TELECOMMUTE", "https://schema.org/TELECOMMUTE", "http://schema.org/TELECOMMUTE"}


@dataclass(frozen=True)
class JobAd:
    title: str
    location: str
    date_posted: date | None
    valid_through: date | None
    employment_type: str
    url: str
    dedupe_key: str
    page_url: str  # the page it was read on


@dataclass
class QualityReport:
    accepted: list[JobAd] = field(default_factory=list)
    rejected: Counter[str] = field(default_factory=Counter)

    def merge(self, other: QualityReport) -> None:
        seen = {a.dedupe_key for a in self.accepted}
        for ad in other.accepted:
            if ad.dedupe_key in seen:
                self.rejected["duplicate"] += 1
            else:
                self.accepted.append(ad)
                seen.add(ad.dedupe_key)
        self.rejected.update(other.rejected)


def clean_text(value: Any, limit: int) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value") or ""
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value if isinstance(v, str))
    if not isinstance(value, str):
        return ""
    text = _SPACES.sub(" ", _TAGS.sub(" ", html.unescape(value))).strip()
    return text[:limit]


def parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    try:
        if len(v) == 10:
            return date.fromisoformat(v)
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC).date()
    except ValueError:
        return None


def _location(ad: dict[str, Any]) -> str:
    if str(ad.get("jobLocationType") or "") in REMOTE:
        return "Remote"
    loc = ad.get("jobLocation")
    first = loc[0] if isinstance(loc, list) and loc else loc
    addr = first.get("address") if isinstance(first, dict) else None
    if isinstance(addr, str):
        return clean_text(addr, 100)
    if isinstance(addr, dict):
        parts = [clean_text(addr.get(k), 60) for k in ("addressLocality", "addressRegion", "addressCountry")]
        return ", ".join(dict.fromkeys(p for p in parts if p))[:100]
    return ""


def same_employer(org_name: str, company_name: str) -> bool:
    """The ad's employer is the watched company (not a recruitment agency advertising for someone else)."""
    a, b = set(normalise_name(org_name).split()), set(normalise_name(company_name).split())
    if not a or not b:
        return True  # no usable name on the ad: it is on the company's own verified site
    return len(a & b) / min(len(a), len(b)) >= 0.5


def dedupe_key(site: str, title: str, location: str) -> str:
    norm = " ".join(normalise_name(title).split()) + "|" + location.lower()
    return hashlib.sha256(f"{site}|{norm}".encode()).hexdigest()[:32]


def _ad_url(ad: dict[str, Any], page_url: str, site: str) -> str:
    url = ad.get("url")
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        host = (urlsplit(url).hostname or "").lower()
        if on_site(host, site) or ATS_LINK.match(url):
            return url[:2048]
    return page_url  # missing, relative-looking or pointing elsewhere: the page we read it on


def validate_job_ads(
    objs: list[dict[str, Any]], *, page_url: str, site: str, company_name: str, today: date
) -> QualityReport:
    report = QualityReport()
    seen: set[str] = set()
    for ad in of_type(objs, "JobPosting"):
        title = clean_text(ad.get("title") or ad.get("name"), MAX_TITLE + 1)
        if len(title) < 3:
            report.rejected["missing_title"] += 1
            continue
        if len(title) > MAX_TITLE:
            report.rejected["title_too_long"] += 1
            continue
        if _EMAILISH.search(title) or _PHONEISH.search(title):
            report.rejected["contact_details_in_title"] += 1  # never store contact data, even by accident
            continue
        org = ad.get("hiringOrganization")
        org_name = clean_text(org.get("name") if isinstance(org, dict) else org, 200)
        if org_name and not same_employer(org_name, company_name):
            report.rejected["other_employer"] += 1
            continue
        posted, until = parse_date(ad.get("datePosted")), parse_date(ad.get("validThrough"))
        if posted and posted > today + MAX_FUTURE:
            report.rejected["date_in_future"] += 1
            continue
        if posted and posted < today - MAX_AGE:
            report.rejected["stale"] += 1
            continue
        if until and until < today:
            report.rejected["expired"] += 1
            continue
        location = _location(ad)
        key = dedupe_key(site, title, location)
        if key in seen:
            report.rejected["duplicate"] += 1
            continue
        seen.add(key)
        report.accepted.append(
            JobAd(
                title=title,
                location=location,
                date_posted=posted,
                valid_through=until,
                employment_type=clean_text(ad.get("employmentType"), 40),
                url=_ad_url(ad, page_url, site),
                dedupe_key=key,
                page_url=page_url,
            )
        )
    return report
