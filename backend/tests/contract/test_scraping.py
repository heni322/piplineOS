"""Web collection (ADR-0020) against simulated websites: the polite fetcher's rules, extraction, data quality and
the careers-page source end to end. No network is touched."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.adapters.base import RateLimited, RetryableProviderError
from app.adapters.signals.career_pages import CareerPageSource, WatchedSite
from app.scraping.extract import ats_board, career_links, parse_html, parse_sitemap
from app.scraping.fetcher import Blocked, BudgetExhausted, CrawlBudget, PoliteFetcher
from app.scraping.quality import JobAd, validate_job_ads
from app.scraping.store import MemoryScrapeStore

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
TODAY = NOW.date()
Route = tuple[int, dict[str, str], str] | Callable[[httpx.Request], httpx.Response]


class Site:
    """A fake web: {(host, path): (status, headers, body)}; records every request."""

    def __init__(self, routes: dict[tuple[str, str], Route]) -> None:
        self.routes, self.calls = routes, []  # type: ignore[var-annotated]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        r = self.routes.get((req.url.host, req.url.path))
        if r is None:
            return httpx.Response(404, text="not found")
        if callable(r):
            return r(req)
        status, headers, body = r
        return httpx.Response(status, headers={"content-type": "text/html; charset=utf-8", **headers}, text=body)

    def paths(self) -> list[str]:
        return [f"{c.url.host}{c.url.path}" for c in self.calls]


class Clock:
    def __init__(self) -> None:
        self.t, self.slept = 1000.0, []  # type: ignore[var-annotated]

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(round(s, 2))
        self.t += s


def fetcher(site: Site, clock: Clock | None = None, **kw: Any) -> PoliteFetcher:
    c = clock or Clock()
    return PoliteFetcher(transport=httpx.MockTransport(site), public_host=lambda h: True, clock=c, sleep=c.sleep, **kw)


def jsonld(*objs: dict[str, Any]) -> str:
    return "".join(f'<script type="application/ld+json">{json.dumps(o)}</script>' for o in objs)


def ad(title: str, posted: str | None = "2026-10-14", **kw: Any) -> dict[str, Any]:
    o: dict[str, Any] = {"@context": "https://schema.org", "@type": "JobPosting", "title": title}
    if posted:
        o["datePosted"] = posted
    o["hiringOrganization"] = {"@type": "Organization", "name": kw.pop("org", "Lakeside Networks Ltd")}
    o["jobLocation"] = {"@type": "Place", "address": {"addressLocality": "Leeds", "addressCountry": "GB"}}
    return o | kw


# ------------------------------------------------------------------------------------------- fetcher rules
def test_robots_rules_and_crawl_delay_are_obeyed_and_robots_is_fetched_once() -> None:
    web = Site(
        {
            ("lakeside.example", "/robots.txt"): (
                200,
                {"content-type": "text/plain"},
                "User-agent: *\nDisallow: /private\nCrawl-delay: 5\n",
            ),
            ("lakeside.example", "/careers"): (200, {}, "<html>ok</html>"),
        }
    )
    clock = Clock()
    f = fetcher(web, clock)
    assert f.fetch("https://lakeside.example/careers", site="lakeside.example").ok
    assert f.fetch("https://lakeside.example/careers", site="lakeside.example").ok
    with pytest.raises(Blocked, match="robots"):
        f.fetch("https://lakeside.example/private/x", site="lakeside.example")
    assert web.paths().count("lakeside.example/robots.txt") == 1
    assert 5.0 in clock.slept  # the site's Crawl-delay, longer than our 2 s default


@pytest.mark.parametrize(("status", "allowed"), [(404, True), (410, True), (403, False), (401, False), (500, False)])
def test_robots_status_handling_follows_rfc_9309(status: int, allowed: bool) -> None:
    web = Site(
        {
            ("x.example", "/robots.txt"): (status, {}, ""),
            ("x.example", "/careers"): (200, {}, "<html>ok</html>"),
        }
    )
    f = fetcher(web)
    if allowed:
        assert f.fetch("https://x.example/careers", site="x.example").ok
    else:
        with pytest.raises(Blocked):
            f.fetch("https://x.example/careers", site="x.example")


def test_robots_unreachable_means_stay_away_for_now() -> None:
    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(Blocked):
        fetcher(Site({("x.example", "/robots.txt"): down})).fetch("https://x.example/careers", site="x.example")


def test_the_crawl_stays_on_the_site_and_on_the_public_internet() -> None:
    web = Site(
        {
            ("x.example", "/jobs"): (302, {"location": "https://evil.example/steal"}, ""),
            ("x.example", "/careers"): (301, {"location": "https://www.x.example/careers"}, ""),
            ("www.x.example", "/careers"): (200, {}, "<html>moved</html>"),
        }
    )
    f = fetcher(web)
    with pytest.raises(Blocked, match="off_site"):
        f.fetch("https://x.example/jobs", site="x.example")
    assert f.fetch("https://x.example/careers", site="x.example").url == "https://www.x.example/careers"
    for url in ("https://x.example:8080/careers", "ftp://x.example/careers", "https://user@x.example/"):
        with pytest.raises(Blocked, match="unsupported_url"):
            f.fetch(url, site="x.example")
    private = PoliteFetcher(transport=httpx.MockTransport(web), public_host=lambda h: False)
    with pytest.raises(Blocked, match="not_public"):  # e.g. a domain resolving to 10.0.0.5 or 169.254.169.254
        private.fetch("https://x.example/careers", site="x.example")


def test_size_and_content_type_limits() -> None:
    web = Site(
        {
            ("x.example", "/big"): (200, {}, "<p>" + "a" * 5000),
            ("x.example", "/brochure.pdf"): (200, {"content-type": "application/pdf"}, "%PDF"),
        }
    )
    f = fetcher(web, max_bytes=1000)
    big = f.fetch("https://x.example/big", site="x.example")
    assert big.truncated and len(big.text) == 1000
    pdf = f.fetch("https://x.example/brochure.pdf", site="x.example")
    assert pdf.status == 200 and not pdf.text  # not read: not a type we parse


def test_conditional_get_makes_an_unchanged_page_cost_one_304() -> None:
    def page(req: httpx.Request) -> httpx.Response:
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, headers={"etag": '"v1"', "content-type": "text/html"}, text="<html>jobs</html>")

    f = fetcher(Site({("x.example", "/careers"): page}))
    assert f.fetch("https://x.example/careers", site="x.example").ok
    again = f.fetch("https://x.example/careers", site="x.example")
    assert again.not_modified and not again.ok


def test_failures_defer_the_site_and_a_failing_host_opens_its_circuit() -> None:
    web = Site(
        {
            ("x.example", "/busy"): (503, {"retry-after": "120"}, ""),
            ("x.example", "/broken"): (500, {}, ""),
        }
    )
    f = fetcher(web)
    with pytest.raises(RateLimited):
        f.fetch("https://x.example/busy", site="x.example")
    for _ in range(2):
        with pytest.raises(RetryableProviderError):
            f.fetch("https://x.example/broken", site="x.example")
    before = len(web.calls)
    with pytest.raises(RetryableProviderError, match="circuit open"):
        f.fetch("https://x.example/broken", site="x.example")
    assert len(web.calls) == before  # the host is left alone for its cooldown


def test_pacing_is_per_host_and_the_budget_ends_the_run() -> None:
    web = Site({(h, "/careers"): (200, {}, "<html></html>") for h in ("a.example", "b.example")})
    clock = Clock()
    f = fetcher(web, clock, budget=CrawlBudget(max_requests=5))
    f.fetch("https://a.example/careers", site="a.example")  # robots + page
    f.fetch("https://b.example/careers", site="b.example")  # another host: no wait
    # each host waited only between its own two requests (robots.txt, then the page); never for the other host
    assert clock.slept == [2.0, 2.0]
    f.fetch("https://a.example/careers", site="a.example")
    with pytest.raises(BudgetExhausted):
        f.fetch("https://b.example/careers", site="b.example")


# ------------------------------------------------------------------------------------------- extraction
def test_jsonld_is_found_in_graphs_lists_and_cdata_and_broken_blocks_are_counted() -> None:
    html = (
        jsonld({"@graph": [{"@type": "Organization", "name": "Lakeside"}, ad("Service Desk Analyst")]})
        + jsonld([ad("Network Engineer")])  # type: ignore[arg-type]
        + '<script type="application/ld+json">//<![CDATA[\n'
        + json.dumps(ad("Cloud Engineer"))
        + "\n//]]></script>"
        + '<script type="application/ld+json">{not json</script>'
    )
    parsed = parse_html(html, "https://lakeside.example/careers")
    titles = [o.get("title") for o in parsed.jsonld if o.get("@type") == "JobPosting"]
    assert titles == ["Service Desk Analyst", "Network Engineer", "Cloud Engineer"]
    assert parsed.json_errors == 1


def test_career_links_and_hosted_boards_are_found_by_where_they_point_not_by_layout() -> None:
    html = """<a href="/about">About</a><a href="/work-with-us">Join our team</a><a href="/news/vacancy-filled">x</a>
      <a href="https://other.example/careers">Partner jobs</a><a href="mailto:hr@lakeside.example">HR</a>
      <a href="https://boards.greenhouse.io/lakeside?gh_src=x">See all openings</a>"""
    parsed = parse_html(html, "https://lakeside.example/")
    assert career_links(parsed.links, "lakeside.example")[0] == "https://lakeside.example/work-with-us"
    assert "https://other.example/careers" not in career_links(parsed.links, "lakeside.example")
    assert ats_board(parsed.links) == "greenhouse:lakeside"
    assert ats_board(parse_html('<a href="https://jobs.eu.lever.co/Acme">x</a>', "https://a.example").links) == (
        "lever-eu:acme"
    )


def test_sitemaps_are_parsed_safely_and_stay_on_site() -> None:
    index = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://lakeside.example/sitemap-jobs.xml</loc></sitemap></sitemapindex>"""
    assert parse_sitemap(index, "lakeside.example").children == ["https://lakeside.example/sitemap-jobs.xml"]
    pages = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://lakeside.example/careers/</loc></url><url><loc>https://evil.example/x</loc></url></urlset>"""
    assert parse_sitemap(pages, "lakeside.example").pages == ["https://lakeside.example/careers/"]
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset><url><loc>&a;</loc></url></urlset>'
    assert parse_sitemap(bomb, "lakeside.example").pages == []  # entities refused (defusedxml)


# ------------------------------------------------------------------------------------------- data quality
def _check(*objs: dict[str, Any]) -> Any:
    return validate_job_ads(
        list(objs),
        page_url="https://lakeside.example/careers",
        site="lakeside.example",
        company_name="Lakeside Networks Ltd",
        today=TODAY,
    )


def test_quality_rules_accept_clean_ads_and_count_every_rejection() -> None:
    r = _check(
        ad("1st Line Support Engineer", url="https://lakeside.example/careers/1"),
        ad("1st Line  Support Engineer"),  # same ad, other spacing: a duplicate
        ad("Expired role", validThrough="2026-09-30"),
        ad("Old role", posted="2025-01-10"),
        ad("Future role", posted="2027-01-01"),
        ad("Field Engineer", org="Hays Recruitment"),  # an agency advertising for someone else
        ad("Call 0113 496 0000 now"),
        ad(""),
        ad("Remote DevOps Engineer", posted=None, jobLocationType="TELECOMMUTE", url="https://evil.example/x"),
    )
    assert [(a.title, a.location) for a in r.accepted] == [
        ("1st Line Support Engineer", "Leeds, GB"),
        ("Remote DevOps Engineer", "Remote"),
    ]
    assert dict(r.rejected) == {
        "duplicate": 1,
        "expired": 1,
        "stale": 1,
        "date_in_future": 1,
        "other_employer": 1,
        "contact_details_in_title": 1,
        "missing_title": 1,
    }
    assert r.accepted[0].url == "https://lakeside.example/careers/1"
    assert r.accepted[1].url == "https://lakeside.example/careers"  # an off-site URL is never trusted
    assert r.accepted[1].date_posted is None


def test_nothing_personal_is_ever_kept() -> None:
    r = _check(
        ad(
            "Service Desk Analyst",
            description="Contact Jane Smith on jane.smith@lakeside.example or 07700 900123",
            applicantContact={"name": "Jane Smith", "email": "jane.smith@lakeside.example"},
            baseSalary={"value": 30000},
        )
    )
    kept = json.dumps([asdict(a) for a in r.accepted], default=str)
    assert "Jane" not in kept and "@" not in kept and "07700" not in kept and "30000" not in kept
    assert set(JobAd.__dataclass_fields__) == {
        "title",
        "location",
        "date_posted",
        "valid_through",
        "employment_type",
        "url",
        "dedupe_key",
        "page_url",
    }


# ------------------------------------------------------------------------------------------- the source
def lakeside(extra_ads: list[dict[str, Any]] | None = None, *, home_status: int = 200) -> Site:
    listing = '<h1>Vacancies</h1><a href="/careers/service-desk">Service Desk Analyst</a>'
    listing += '<a href="/careers/net-eng">Network Engineer</a><a href="/careers/accountant">Accountant</a>'
    return Site(
        {
            ("lakeside.example", "/robots.txt"): (200, {"content-type": "text/plain"}, "User-agent: *\nAllow: /\n"),
            ("lakeside.example", "/"): (home_status, {}, '<a href="/careers">Careers</a><a href="/about">About</a>'),
            ("lakeside.example", "/careers"): (200, {}, listing),
            ("lakeside.example", "/careers/service-desk"): (200, {}, jsonld(ad("Service Desk Analyst"))),
            ("lakeside.example", "/careers/net-eng"): (
                200,
                {},
                jsonld(ad("Network Engineer", posted=None), *(extra_ads or [])),
            ),
            ("lakeside.example", "/careers/accountant"): (200, {}, jsonld(ad("Accountant"))),
        }
    )


def source(web: Site, store: MemoryScrapeStore, now: list[datetime], **kw: Any) -> CareerPageSource:
    clock = Clock()
    f = PoliteFetcher(
        transport=httpx.MockTransport(web), public_host=lambda h: True, cache=store, clock=clock, sleep=clock.sleep
    )
    sites = kw.pop("sites", [WatchedSite("Lakeside Networks Ltd", "www.Lakeside.example", "01234567", "GB")])
    return CareerPageSource(sites, store=store, fetcher=f, clock=lambda: now[0], workers=1, **kw)


QUERY = "engineer; analyst; -accountant"


def test_careers_pages_become_a_hiring_signal_without_reporting_what_was_already_there() -> None:
    store, now = MemoryScrapeStore(), [NOW]
    src = source(lakeside(), store, now)
    sigs = src.fetch(QUERY, NOW - timedelta(days=2))
    # dated ad in the window: new. Undated ad seen on the very first crawl: a baseline, not news. Accountant: excluded.
    assert len(sigs) == 1 and sigs[0].text.endswith("on its own website: Service Desk Analyst, Leeds, GB.")
    assert sigs[0].company_number == "01234567" and sigs[0].domain == "lakeside.example"
    assert src.metrics["sites_discovered"] == 1 and src.metrics["ads_accepted"] == 3

    web2 = lakeside([ad("Cloud Engineer", posted=None)])
    now[0] = NOW + timedelta(hours=2)  # not due yet: nothing is fetched at all
    again = source(web2, store, now)
    assert again.fetch(QUERY, now[0] - timedelta(days=2)) == [] and web2.calls == []
    assert again.metrics["fresh"] == 1

    now[0] = NOW + timedelta(days=1)  # due: the undated ad that appeared since is new; the baseline one is not
    later = source(web2, store, now)
    [sig] = later.fetch(QUERY, now[0] - timedelta(days=2))
    assert "Cloud Engineer" in sig.text and "Network Engineer" not in sig.text
    assert "lakeside.example/" not in web2.paths()  # discovery is remembered for a month: straight to /careers


def test_a_hosted_board_linked_from_the_careers_page_is_read_through_its_free_api() -> None:
    web = Site(
        {
            ("lakeside.example", "/robots.txt"): (404, {}, ""),
            ("lakeside.example", "/"): (200, {}, '<a href="/join-us">Join us</a>'),
            ("lakeside.example", "/join-us"): (200, {}, '<a href="https://jobs.ashbyhq.com/lakeside">Openings</a>'),
        }
    )
    published = (NOW - timedelta(days=1)).isoformat()
    ashby = httpx.MockTransport(
        lambda r: httpx.Response(
            200, json={"jobs": [{"title": "Cloud Engineer", "publishedAt": published, "id": "a1"}]}
        )
    )
    store = MemoryScrapeStore()
    [sig] = source(web, store, [NOW], board_transports={"ashby": ashby}).fetch(QUERY, NOW - timedelta(days=2))
    assert "Cloud Engineer" in sig.text
    assert store.sites["lakeside.example"].ats_board == "ashby:lakeside"


def test_a_site_that_is_down_backs_off_and_never_stops_the_others() -> None:
    web = lakeside()
    web.routes[("down.example", "/robots.txt")] = (200, {"content-type": "text/plain"}, "")
    web.routes[("down.example", "/")] = (500, {}, "")
    store = MemoryScrapeStore()
    sites = [
        WatchedSite("Down Ltd", "down.example"),
        WatchedSite("Lakeside Networks Ltd", "lakeside.example"),
    ]
    src = source(web, store, [NOW], sites=sites)
    sigs = src.fetch(QUERY, NOW - timedelta(days=2))
    assert [s.company_name for s in sigs] == ["Lakeside Networks Ltd"]
    assert src.failed == ["down.example"]
    down = store.sites["down.example"]
    assert down.consecutive_failures == 1 and down.next_crawl_at is not None and down.next_crawl_at > NOW


def test_a_site_whose_robots_txt_says_no_is_left_alone() -> None:
    web = Site(
        {("private.example", "/robots.txt"): (200, {"content-type": "text/plain"}, "User-agent: *\nDisallow: /")}
    )
    store = MemoryScrapeStore()
    src = source(web, store, [NOW], sites=[WatchedSite("Private Ltd", "private.example")])
    assert src.fetch("", NOW - timedelta(days=2)) == []
    assert web.paths() == ["private.example/robots.txt"]  # no page was requested
    assert store.sites["private.example"].next_crawl_at == NOW + timedelta(hours=20 * 7)


def test_the_diagnostic_reports_where_the_ads_are_and_why_any_were_rejected() -> None:
    report = source(lakeside([ad("Expired", validThrough="2026-01-01")]), MemoryScrapeStore(), [NOW]).diagnose(
        "lakeside.example", "Lakeside Networks Ltd"
    )
    assert report["career_urls"] == ["https://lakeside.example/careers"]
    assert ("Service Desk Analyst", "Leeds, GB", "2026-10-14") in report["ads"]  # type: ignore[operator]
    assert report["rejected"] == {"expired": 1}
    assert isinstance(report["requests"], dict) and report["requests"]["requests"] >= 5


def test_dates_without_time_zone_and_odd_formats_are_handled() -> None:
    from app.scraping.quality import parse_date

    assert parse_date("2026-10-14T09:00:00") == date(2026, 10, 14)
    assert parse_date("2026-10-14T23:30:00-05:00") == date(2026, 10, 15)
    assert parse_date("14/10/2026") is None and parse_date(None) is None
