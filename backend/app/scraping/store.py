"""What the crawler has seen, and when (ADR-0020). Two implementations of one interface: the database (production,
shared by every client and every worker) and memory (the `signals check` command and tests: nothing stored)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.session import DbContext, transaction
from app.scraping.fetcher import FetchResult
from app.scraping.quality import EXTRACTOR_VERSION, JobAd

RETAIN_UNSEEN = timedelta(days=180)


@dataclass(frozen=True)
class SiteState:
    career_urls: list[str]
    ats_board: str | None
    discovered_at: datetime | None
    last_crawled_at: datetime | None
    next_crawl_at: datetime | None
    consecutive_failures: int


@dataclass(frozen=True)
class SeenAd:
    ad: JobAd
    first_seen_at: datetime
    new: bool  # first time this ad was ever seen
    baseline: bool  # already on the site at its first crawl, before we started watching (persisted)


class ScrapeStore(Protocol):
    def site(self, site: str) -> SiteState | None: ...

    def save_discovery(self, site: str, career_urls: list[str], ats_board: str | None, now: datetime) -> None: ...

    def crawled(self, site: str, *, ok: bool, outcome: str, now: datetime, next_crawl_at: datetime) -> None: ...

    def record_ads(self, site: str, ads: list[JobAd], now: datetime, *, baseline: bool) -> list[SeenAd]: ...

    def touch_page(self, site: str, page_url: str, now: datetime) -> None: ...

    def validators(self, url: str) -> tuple[str | None, str | None] | None: ...

    def remember(self, url: str, site: str, result: FetchResult) -> None: ...


class DbScrapeStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def site(self, site: str) -> SiteState | None:
        with transaction(DbContext.system(), self.engine) as conn:
            r = conn.execute(
                text("""SELECT career_urls, ats_board, discovered_at, last_crawled_at, next_crawl_at,
                  consecutive_failures FROM scrape_sites WHERE site = :s"""),
                {"s": site},
            ).first()
        return SiteState(list(r[0]), r[1], r[2], r[3], r[4], r[5]) if r else None

    def save_discovery(self, site: str, career_urls: list[str], ats_board: str | None, now: datetime) -> None:
        with transaction(DbContext.system(), self.engine) as conn:
            conn.execute(
                text("""INSERT INTO scrape_sites (site, career_urls, ats_board, discovered_at, updated_at)
                  VALUES (:s, :u, :a, :n, :n) ON CONFLICT (site) DO UPDATE SET career_urls = EXCLUDED.career_urls,
                  ats_board = EXCLUDED.ats_board, discovered_at = EXCLUDED.discovered_at, updated_at = :n"""),
                {"s": site, "u": career_urls, "a": ats_board, "n": now},
            )

    def crawled(self, site: str, *, ok: bool, outcome: str, now: datetime, next_crawl_at: datetime) -> None:
        with transaction(DbContext.system(), self.engine) as conn:
            conn.execute(
                text("""INSERT INTO scrape_sites (site, last_crawled_at, next_crawl_at, consecutive_failures,
                    last_outcome, updated_at)
                  VALUES (:s, CASE WHEN :ok THEN CAST(:n AS timestamptz) END, :nx, CASE WHEN :ok THEN 0 ELSE 1 END,
                    :o, :n)
                  ON CONFLICT (site) DO UPDATE SET
                    last_crawled_at = CASE WHEN :ok THEN CAST(:n AS timestamptz) ELSE scrape_sites.last_crawled_at END,
                    next_crawl_at = :nx, last_outcome = :o, updated_at = :n,
                    consecutive_failures = CASE WHEN :ok THEN 0 ELSE scrape_sites.consecutive_failures + 1 END"""),
                {"s": site, "ok": ok, "n": now, "nx": next_crawl_at, "o": outcome[:100]},
            )
            if ok:  # minimisation: ads the site no longer shows are forgotten after a while
                conn.execute(
                    text("DELETE FROM scraped_job_ads WHERE site = :s AND last_seen_at < :cut"),
                    {"s": site, "cut": now - RETAIN_UNSEEN},
                )

    def record_ads(self, site: str, ads: list[JobAd], now: datetime, *, baseline: bool) -> list[SeenAd]:
        out = []
        with transaction(DbContext.system(), self.engine) as conn:
            for ad in ads:
                first, inserted, is_baseline = conn.execute(
                    text("""INSERT INTO scraped_job_ads (site, dedupe_key, title, location, employment_type,
                        date_posted, valid_through, ad_url, page_url, baseline, extractor_version, first_seen_at,
                        last_seen_at)
                      VALUES (:s, :k, :t, :l, :e, :dp, :vt, :u, :p, :b, :v, :n, :n)
                      ON CONFLICT (site, dedupe_key) DO UPDATE SET last_seen_at = :n, title = EXCLUDED.title,
                        date_posted = EXCLUDED.date_posted, valid_through = EXCLUDED.valid_through,
                        ad_url = EXCLUDED.ad_url, page_url = EXCLUDED.page_url,
                        extractor_version = EXCLUDED.extractor_version
                      RETURNING first_seen_at, (xmax = 0), scraped_job_ads.baseline"""),
                    {
                        "s": site,
                        "k": ad.dedupe_key,
                        "t": ad.title,
                        "l": ad.location,
                        "e": ad.employment_type,
                        "dp": ad.date_posted,
                        "vt": ad.valid_through,
                        "u": ad.url,
                        "p": ad.page_url,
                        "b": baseline,
                        "v": EXTRACTOR_VERSION,
                        "n": now,
                    },
                ).one()
                out.append(SeenAd(ad, first, bool(inserted), bool(is_baseline)))
        return out

    def touch_page(self, site: str, page_url: str, now: datetime) -> None:
        """The page answered 304: its ads are unchanged, so they are still on the site."""
        with transaction(DbContext.system(), self.engine) as conn:
            conn.execute(
                text("UPDATE scraped_job_ads SET last_seen_at = :n WHERE site = :s AND page_url = :p"),
                {"n": now, "s": site, "p": page_url},
            )

    def validators(self, url: str) -> tuple[str | None, str | None] | None:
        with transaction(DbContext.system(), self.engine) as conn:
            r = conn.execute(
                text("SELECT etag, last_modified FROM scrape_pages WHERE url = :u AND status = 200"), {"u": url}
            ).first()
        return (r[0], r[1]) if r and (r[0] or r[1]) else None

    def remember(self, url: str, site: str, result: FetchResult) -> None:
        if result.not_modified or len(url) > 2048:
            return
        with transaction(DbContext.system(), self.engine) as conn:
            conn.execute(
                text("""INSERT INTO scrape_pages (url, site, status, etag, last_modified, content_sha256)
                  VALUES (:u, :s, :st, :e, :lm, :h) ON CONFLICT (url) DO UPDATE SET status = EXCLUDED.status,
                  etag = EXCLUDED.etag, last_modified = EXCLUDED.last_modified,
                  content_sha256 = EXCLUDED.content_sha256, fetched_at = now()"""),
                {
                    "u": url,
                    "s": site,
                    "st": result.status,
                    "e": (result.etag or None) and result.etag[:300],
                    "lm": (result.last_modified or None) and result.last_modified[:100],
                    "h": result.sha256 if result.status == 200 else None,
                },
            )


@dataclass
class MemoryScrapeStore:
    sites: dict[str, SiteState] = field(default_factory=dict)
    ads: dict[tuple[str, str], tuple[datetime, bool]] = field(default_factory=dict)
    pages: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)

    def site(self, site: str) -> SiteState | None:
        return self.sites.get(site)

    def save_discovery(self, site: str, career_urls: list[str], ats_board: str | None, now: datetime) -> None:
        old = self.sites.get(site)
        self.sites[site] = SiteState(
            career_urls,
            ats_board,
            now,
            old.last_crawled_at if old else None,
            old.next_crawl_at if old else None,
            old.consecutive_failures if old else 0,
        )

    def crawled(self, site: str, *, ok: bool, outcome: str, now: datetime, next_crawl_at: datetime) -> None:
        old = self.sites.get(site) or SiteState([], None, None, None, None, 0)
        self.sites[site] = SiteState(
            old.career_urls,
            old.ats_board,
            old.discovered_at,
            now if ok else old.last_crawled_at,
            next_crawl_at,
            0 if ok else old.consecutive_failures + 1,
        )

    def record_ads(self, site: str, ads: list[JobAd], now: datetime, *, baseline: bool) -> list[SeenAd]:
        out = []
        for ad in ads:
            key = (site, ad.dedupe_key)
            new = key not in self.ads
            first, is_baseline = self.ads.setdefault(key, (now, baseline))
            out.append(SeenAd(ad, first, new, is_baseline))
        return out

    def touch_page(self, site: str, page_url: str, now: datetime) -> None:
        return None

    def validators(self, url: str) -> tuple[str | None, str | None] | None:
        return self.pages.get(url)

    def remember(self, url: str, site: str, result: FetchResult) -> None:
        if result.status == 200 and (result.etag or result.last_modified):
            self.pages[url] = (result.etag, result.last_modified)
