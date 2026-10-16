"""Web collection state on PostgreSQL (ADR-0020): incremental crawl state, first-seen / baseline semantics, 304s
keeping ads alive, minimisation, and the careers-page source wired into a client's hunt."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app.adapters.registry import live_sources
from app.adapters.signals.career_pages import CareerPageSource, WatchedSite
from app.db.session import DbContext, transaction
from app.demo.dataset import DEMO_CONFIG
from app.domain.tenants.config import WatchedCompany
from app.scraping.fetcher import FetchResult, PoliteFetcher
from app.scraping.quality import JobAd, dedupe_key
from app.scraping.store import RETAIN_UNSEEN, DbScrapeStore
from tests.contract.test_scraping import QUERY, Clock, lakeside

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
SITE = "lakeside.example"


def _ad(title: str, page: str = "https://lakeside.example/careers") -> JobAd:
    return JobAd(title, "Leeds", None, None, "", page, dedupe_key(SITE, title, "Leeds"), page)


def _q(engine, sql: str, **p):  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(), engine) as conn:
        return conn.execute(text(sql), p).all()


def test_first_seen_and_baseline_are_persisted_across_runs(clean_db) -> None:  # type: ignore[no-untyped-def]
    store = DbScrapeStore(clean_db)
    [first] = store.record_ads(SITE, [_ad("Network Engineer")], NOW, baseline=True)
    assert first.new and first.baseline and first.first_seen_at == NOW
    later = NOW + timedelta(days=1)
    again, fresh = store.record_ads(SITE, [_ad("Network Engineer"), _ad("Cloud Engineer")], later, baseline=False)
    assert not again.new and again.baseline and again.first_seen_at == NOW  # still a baseline ad: never "new"
    assert fresh.new and not fresh.baseline and fresh.first_seen_at == later


def test_crawl_state_backoff_and_minimisation(clean_db) -> None:  # type: ignore[no-untyped-def]
    store = DbScrapeStore(clean_db)
    store.save_discovery(SITE, ["https://lakeside.example/careers"], None, NOW)
    store.crawled(SITE, ok=False, outcome="unavailable", now=NOW, next_crawl_at=NOW + timedelta(hours=5))
    store.crawled(SITE, ok=False, outcome="unavailable", now=NOW, next_crawl_at=NOW + timedelta(hours=10))
    st = store.site(SITE)
    assert st is not None and st.consecutive_failures == 2 and st.last_crawled_at is None
    assert st.career_urls == ["https://lakeside.example/careers"]
    store.record_ads(SITE, [_ad("Old role"), _ad("Live role")], NOW - RETAIN_UNSEEN - timedelta(days=1), baseline=True)
    store.record_ads(SITE, [_ad("Live role")], NOW, baseline=False)
    store.crawled(SITE, ok=True, outcome="ok:1", now=NOW, next_crawl_at=NOW + timedelta(hours=20))
    st = store.site(SITE)
    assert st is not None and st.consecutive_failures == 0 and st.last_crawled_at == NOW
    assert [r.title for r in _q(clean_db, "SELECT title FROM scraped_job_ads")] == ["Live role"]


def test_a_304_keeps_that_pages_ads_alive_and_validators_are_reused(clean_db) -> None:  # type: ignore[no-untyped-def]
    store = DbScrapeStore(clean_db)
    page = "https://lakeside.example/careers/net"
    store.record_ads(SITE, [_ad("Network Engineer", page), _ad("Other page role")], NOW, baseline=True)
    later = NOW + timedelta(days=90)
    store.touch_page(SITE, page, later)
    seen = dict(_q(clean_db, "SELECT title, last_seen_at FROM scraped_job_ads"))
    assert seen == {"Network Engineer": later, "Other page role": NOW}
    store.remember(page, SITE, FetchResult(page, 200, "text/html", "<html/>", etag='"v7"'))
    assert store.validators(page) == ('"v7"', None)
    store.remember(page, SITE, FetchResult(page, 404, "text/html", ""))
    assert store.validators(page) is None  # gone: the next request is unconditional


def test_a_full_crawl_runs_against_the_database_store(clean_db) -> None:  # type: ignore[no-untyped-def]
    store, web, clock = DbScrapeStore(clean_db), lakeside(), Clock()
    f = PoliteFetcher(transport=httpx.MockTransport(web), public_host=lambda h: True, cache=store, sleep=clock.sleep)
    src = CareerPageSource(
        [WatchedSite("Lakeside Networks Ltd", SITE)], store=store, fetcher=f, clock=lambda: NOW, workers=2
    )
    [sig] = src.fetch(QUERY, NOW - timedelta(days=2))
    assert "Service Desk Analyst" in sig.text
    rows = _q(clean_db, "SELECT title, baseline, extractor_version FROM scraped_job_ads ORDER BY title")
    assert [(r.title, r.baseline) for r in rows] == [
        ("Accountant", True),
        ("Network Engineer", True),
        ("Service Desk Analyst", True),
    ]
    assert {r.extractor_version for r in rows} == {"jobposting-jsonld/1"}
    assert _q(clean_db, "SELECT last_outcome FROM scrape_sites")[0].last_outcome == "ok:3"
    cols = {
        r[0]
        for r in _q(clean_db, "SELECT column_name FROM information_schema.columns WHERE table_name = 'scraped_job_ads'")
    }
    assert not {"description", "email", "contact", "salary", "name"} & cols  # company data only


def test_watched_companies_without_a_job_board_are_crawled_on_their_own_site(clean_db, settings) -> None:  # type: ignore[no-untyped-def]
    cfg = DEMO_CONFIG.model_copy(
        update={
            "watchlist": [
                WatchedCompany(name="Lakeside Networks Ltd", domain="lakeside.example"),
                WatchedCompany(name="Contoso", domain="contoso.example", job_board="greenhouse:contoso"),
                WatchedCompany(name="No Site Ltd"),
            ]
        }
    )
    names = [b.source.name for b in live_sources(cfg, settings, clean_db)]
    assert names.count("career_pages.live") == 1 and "job_boards.live" in names
    [career] = [b.source for b in live_sources(cfg, settings, clean_db) if b.source.name == "career_pages.live"]
    assert [s.domain for s in career._sites] == ["lakeside.example"]  # type: ignore[attr-defined]
    off = settings.model_copy(update={"career_pages": False})
    assert "career_pages.live" not in [b.source.name for b in live_sources(cfg, off, clean_db)]
