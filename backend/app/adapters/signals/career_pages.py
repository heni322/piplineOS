"""Hiring signals from the watched companies' OWN careers pages (ADR-0020).

Most UK IT service companies do not use Greenhouse, Lever or Ashby: they list vacancies on their own website.
Employers who want their ads on Google for Jobs publish each one as schema.org `JobPosting` structured data, so we
read that, not the page layout. For a watched company with a website and no known job board:

1. **Discover** (once a month): the homepage's "Careers / Vacancies / Join us" links, the sitemap (from robots.txt
   or /sitemap.xml), else a few usual paths. A link to a hosted board we can read (Greenhouse, Lever, Ashby) is
   remembered and read through its free API instead.
2. **Crawl** (at most once a day per site, a few pages): job ads in JSON-LD; a listing page without them leads to
   its on-site ad pages. Every request goes through the polite fetcher (robots.txt, pacing, limits, breaker).
3. **Quality**: each ad validated, minimised and deduplicated (`app.scraping.quality`).
4. **New?** An ad is new when its datePosted is in the lookback window, or, without a date, when we see it for the
   first time AFTER the site's first crawl (ads already there when we started watching are a baseline, not news).

One signal per company per run, in the same form as the job-board source. A site that is down is retried later
with backoff; a site whose robots.txt says no is left alone.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

import httpx

from app.adapters.base import RateLimited, RetryableProviderError
from app.adapters.signals.base import RawSignal
from app.adapters.signals.job_boards import JobBoardSource, WatchedBoard
from app.adapters.signals.ocds import _matches, parse_query
from app.core.crypto import normalise_domain
from app.core.logging import get_logger
from app.scraping.extract import ats_board, career_links, career_pages_in, of_type, parse_html, parse_sitemap
from app.scraping.fetcher import XML_TYPES, Blocked, BudgetExhausted, FetchResult, PoliteFetcher
from app.scraping.quality import QualityReport, validate_job_ads
from app.scraping.store import ScrapeStore, SeenAd, SiteState

log = get_logger(__name__)
MAX_TITLES = 8
FALLBACK_PATHS = ("/careers", "/jobs", "/vacancies")
_DOMAIN_OK = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


@dataclass(frozen=True)
class WatchedSite:
    company_name: str
    domain: str
    company_number: str | None = None
    country_hint: str | None = None


class CareerPageSource:
    name = "career_pages.live"

    def __init__(
        self,
        sites: list[WatchedSite],
        *,
        store: ScrapeStore,
        fetcher: PoliteFetcher | None = None,
        board_transports: dict[str, httpx.BaseTransport] | None = None,
        max_pages_per_site: int = 8,
        recrawl: timedelta = timedelta(hours=20),
        rediscover: timedelta = timedelta(days=30),
        workers: int = 4,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sites, self._store = sites, store
        self._fetcher = fetcher or PoliteFetcher(cache=store)
        self._board_transports = board_transports
        self._max_pages, self._recrawl, self._rediscover = max_pages_per_site, recrawl, rediscover
        self._workers, self._clock = workers, clock
        self.failed: list[str] = []  # sites to retry (the hunt records the run as "partial")
        self.metrics: Counter[str] = Counter()
        self._lock = Lock()

    # ------------------------------------------------------------------ SignalSource
    def fetch(self, query: str, since: datetime) -> list[RawSignal]:
        include, exclude = parse_query(query)
        with ThreadPoolExecutor(max_workers=max(1, self._workers)) as pool:  # sites in parallel, each host paced
            results = list(pool.map(lambda w: self._one(w, include, exclude, since), self._sites))
        if self.metrics["budget_exhausted"]:
            log.warning("career_pages_budget_exhausted", sites_waiting=self.metrics["budget_exhausted"])
        return [s for r in results for s in r]

    # ------------------------------------------------------------------ per site
    def _one(self, w: WatchedSite, include: list[str], exclude: list[str], since: datetime) -> list[RawSignal]:
        site = normalise_domain(w.domain)
        if not _DOMAIN_OK.match(site):
            self._count("invalid_domain")
            return []
        now = self._clock()
        state = self._store.site(site)
        if state and state.next_crawl_at and state.next_crawl_at > now:
            self._count("fresh")  # crawled recently: nothing to do until it is due (incremental)
            return []
        try:
            urls, board = self._targets(site, state, now)
            if board:
                return self._board(w, site, board, include, exclude, since, now)
            report, pages = self._crawl(site, w.company_name, urls, now)
            if pages.get("board"):  # the careers page links to a hosted board: use it from now on
                self._store.save_discovery(site, urls, pages["board"], now)
                return self._board(w, site, pages["board"], include, exclude, since, now)
            first_crawl = state is None or state.last_crawled_at is None
            seen = self._store.record_ads(site, report.accepted, now, baseline=first_crawl)
            new = [s for s in seen if self._is_new(s, since) and self._wanted(s.ad.title, include, exclude)]
            self._store.crawled(
                site, ok=True, outcome=f"ok:{len(report.accepted)}", now=now, next_crawl_at=now + self._recrawl
            )
            self._count("sites_crawled")
            self._count("ads_accepted", len(report.accepted))
            for reason, n in report.rejected.items():
                self._count(f"rejected_{reason}", n)
            return [self._signal(w, site, new)] if new else []
        except BudgetExhausted:
            self._count("budget_exhausted")  # not marked as crawled: it is still due on the next run
            return []
        except Blocked as exc:  # robots.txt says no: respect it, check again in a week. Not counted as a crawl, so
            # the first crawl it ever allows is still the baseline (its existing ads are not reported as new)
            self._count(f"blocked_{exc.reason}")
            self._store.crawled(
                site, ok=False, outcome=f"blocked:{exc.reason}", now=now, next_crawl_at=now + 7 * self._recrawl
            )
            return []
        except RateLimited:
            raise  # the whole source waits (the hunt records "rate_limited" and tries again next run)
        except RetryableProviderError as exc:  # down or refusing for now: back off this site only
            failures = (state.consecutive_failures if state else 0) + 1
            wait = min(self._recrawl * (2 ** (failures - 1)) / 4, timedelta(days=7))
            self._store.crawled(site, ok=False, outcome="unavailable", now=now, next_crawl_at=now + wait)
            with self._lock:
                self.failed.append(site)
            self._count("sites_unavailable")
            log.info("career_site_unavailable", site=site, error=str(exc)[:120])
            return []

    def _targets(self, site: str, state: SiteState | None, now: datetime) -> tuple[list[str], str | None]:
        if state and state.discovered_at and now - state.discovered_at < self._rediscover:
            return state.career_urls, state.ats_board
        urls, board = self._discover(site)
        self._store.save_discovery(site, urls, board, now)
        self._count("sites_discovered")
        return urls, board

    def _discover(self, site: str) -> tuple[list[str], str | None]:
        home = self._first_ok([f"https://{site}/", f"https://www.{site}/"], site)
        candidates: list[str] = []
        if home is not None:
            parsed = parse_html(home.text, home.url)
            board = ats_board(parsed.links)
            if board:
                return [], board
            if of_type(parsed.jsonld, "JobPosting"):
                candidates.append(home.url)
            candidates += career_links(parsed.links, site)
        if not candidates:
            candidates += self._from_sitemap(site)
        if not candidates:
            ok = self._first_ok([f"https://{site}{p}" for p in FALLBACK_PATHS], site)
            candidates += [ok.url] if ok else []
        return list(dict.fromkeys(candidates))[:3], None

    def _from_sitemap(self, site: str) -> list[str]:
        try:
            maps = self._fetcher.sitemaps(site)[:2] or [f"https://{site}/sitemap.xml"]
            res = self._fetcher.fetch(maps[0], site=site, accept=XML_TYPES)
            if not res.ok:
                return []
            sm = parse_sitemap(res.text, site)
            pages = sm.pages
            for child in sorted(sm.children, key=lambda u: not career_pages_in([u]))[:2]:  # career sitemaps first
                sub = self._fetcher.fetch(child, site=site, accept=XML_TYPES)
                if sub.ok:
                    pages += parse_sitemap(sub.text, site).pages
            return career_pages_in(pages)[:5]
        except Blocked:
            return []

    def _first_ok(self, urls: list[str], site: str) -> FetchResult | None:
        for url in urls:
            try:
                res = self._fetcher.fetch(url, site=site)
            except Blocked as exc:
                if exc.reason == "robots":  # the site said no: never try a variant (www., another path) instead
                    raise
                continue
            if res.ok:
                return res
        return None

    def _crawl(self, site: str, company: str, start: list[str], now: datetime) -> tuple[QualityReport, dict[str, str]]:
        report, extra = QualityReport(), dict[str, str]()
        queue, visited = list(start), set[str]()
        while queue and len(visited) < self._max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                res = self._fetcher.fetch(url, site=site)
            except Blocked:
                continue
            if res.not_modified:
                self._store.touch_page(site, url, now)  # unchanged since last time: its ads are still live
                self._count("pages_not_modified")
                continue
            if not res.ok:
                self._count(f"pages_http_{res.status}")
                continue
            parsed = parse_html(res.text, res.url)
            self._count("pages_read")
            if parsed.json_errors:
                self._count("jsonld_invalid", parsed.json_errors)
            board = ats_board(parsed.links)
            if board:
                extra["board"] = board
                break
            if of_type(parsed.jsonld, "JobPosting"):
                report.merge(
                    validate_job_ads(parsed.jsonld, page_url=res.url, site=site, company_name=company, today=now.date())
                )
            else:  # a listing page: follow its on-site ad links (deeper paths first are usually the ads)
                queue += [u for u in career_links(parsed.links, site) if u not in visited and u not in queue]
        return report, extra

    def _board(
        self,
        w: WatchedSite,
        site: str,
        board: str,
        include: list[str],
        exclude: list[str],
        since: datetime,
        now: datetime,
    ) -> list[RawSignal]:
        jb = JobBoardSource(
            [WatchedBoard(w.company_name, board, site, w.company_number, w.country_hint)],
            transports=self._board_transports,
        )
        query = "; ".join([*include, *(f"-{x}" for x in exclude)])
        sigs = jb.fetch(query, since)
        if jb.failed:
            raise RetryableProviderError(f"job board {board} unavailable")
        self._store.crawled(site, ok=True, outcome=f"board:{board}", now=now, next_crawl_at=now + self._recrawl)
        self._count("sites_via_board")
        return sigs

    def diagnose(self, domain: str, company: str) -> dict[str, object]:
        """One site, live, nothing kept: where its ads are, what was accepted (titles and places only) and why
        anything was rejected. For `pipelineos signals check careers` after setup."""
        site = normalise_domain(domain)
        now = self._clock()
        out: dict[str, object] = {"site": site}
        try:
            urls, board = self._discover(site)
            out |= {"career_urls": urls, "job_board": board}
            if not board:
                report, extra = self._crawl(site, company, urls, now)
                out |= {
                    "job_board_found_on_careers_page": extra.get("board"),
                    "ads": [(a.title, a.location, str(a.date_posted or "")) for a in report.accepted[:20]],
                    "rejected": dict(report.rejected),
                }
        except (Blocked, RetryableProviderError, BudgetExhausted) as exc:
            out["stopped"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        out["requests"] = dict(self._fetcher.stats)
        return out

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _is_new(s: SeenAd, since: datetime) -> bool:
        if s.ad.date_posted is not None:
            return s.ad.date_posted >= since.date()
        return not s.baseline and s.first_seen_at >= since

    @staticmethod
    def _wanted(title: str, include: list[str], exclude: list[str]) -> bool:
        return (not include or _matches(title, include)) and not (exclude and _matches(title, exclude))

    def _signal(self, w: WatchedSite, site: str, new: list[SeenAd]) -> RawSignal:
        def when(s: SeenAd) -> datetime:
            if s.ad.date_posted:
                return datetime(s.ad.date_posted.year, s.ad.date_posted.month, s.ad.date_posted.day, tzinfo=UTC)
            return s.first_seen_at

        new = sorted(new, key=when, reverse=True)
        roles = "; ".join(s.ad.title + (f", {s.ad.location}" if s.ad.location else "") for s in new[:MAX_TITLES])
        more = f" and {len(new) - MAX_TITLES} more" if len(new) > MAX_TITLES else ""
        refs = hashlib.sha256(",".join(sorted(s.ad.dedupe_key for s in new)).encode()).hexdigest()[:16]
        return RawSignal(
            company_name=w.company_name,
            domain=site,
            country_hint=w.country_hint,
            signal_type="job_posting",
            title=f"{w.company_name} is hiring: {len(new)} new role{'s' if len(new) > 1 else ''}"[:200],
            text=f"{w.company_name} published {len(new)} new role(s) on its own website: {roles}{more}."[:1500],
            source_url=new[0].ad.url,
            detected_at=when(new[0]),
            source_record_id=f"career:{site}:{refs}",
            company_number=w.company_number,
        )

    def _count(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.metrics[key] += n
