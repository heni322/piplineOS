"""The polite fetcher: every public web page PipelineOS reads goes through here (ADR-0020).

Rules, enforced in code rather than by convention:
- **Identified.** A stable User-Agent with a URL explaining who we are and how to opt out.
- **robots.txt, per RFC 9309.** Fetched once per host and cached. 2xx: obeyed (including Crawl-delay). 4xx: no
  rules, except 401/403, where we stay away. 5xx or unreachable: everything is treated as disallowed for now.
- **Paced.** At most one request per host every `min_interval` seconds (or the site's Crawl-delay, if longer).
- **Bounded.** Size limit (streamed, whatever the server claims), content types allowed, at most 3 redirects, a
  request budget and a deadline per run.
- **Confined.** Only http(s) on standard ports, only hosts on the site being crawled (or its subdomains), and only
  hosts whose every address is on the public internet (no internal, loopback or link-local addresses: SSRF).
- **Incremental.** Conditional GET with the page's ETag / Last-Modified; an unchanged page costs one 304.
- **Resilient** (ADR-0019). A host that keeps failing opens its own circuit breaker; failures that mean "try
  later" raise RetryableProviderError so the caller defers that site, never decides anything from an outage.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.adapters.base import RateLimited, RetryableProviderError
from app.adapters.http import client
from app.adapters.website_contacts import is_public_host
from app.core.logging import get_logger
from app.core.resilience import CircuitBreaker

log = get_logger(__name__)

BOT_TOKEN = "PipelineOS-bot"  # the product token robots.txt rules are matched against
USER_AGENT = f"Mozilla/5.0 (compatible; {BOT_TOKEN}/1.0; +https://pipelineos.example/bot)"
HTML_TYPES = ("text/html", "application/xhtml+xml")
XML_TYPES = ("application/xml", "text/xml")
MAX_BYTES = 1_000_000
MAX_REDIRECTS = 3
ROBOTS_TTL = 24 * 3600.0
ROBOTS_ERROR_TTL = 15 * 60.0  # a 5xx robots.txt: try again in a while, not on every page
MAX_CRAWL_DELAY = 60.0
REDIRECTS = frozenset({301, 302, 303, 307, 308})


class Blocked(Exception):
    """We chose not to fetch (robots.txt, off-site, private address, unsupported URL). Not an error of the site."""

    def __init__(self, reason: str, url: str) -> None:
        super().__init__(f"{reason}: {url[:200]}")
        self.reason = reason


class BudgetExhausted(Exception):
    """This run's request budget or deadline is used up. The rest waits for the next run."""


@dataclass(frozen=True)
class FetchResult:
    url: str  # after redirects
    status: int
    content_type: str
    text: str
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.not_modified

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8", "replace")).hexdigest()


class PageCache(Protocol):
    """Remembers each page's validators so the next fetch can be conditional."""

    def validators(self, url: str) -> tuple[str | None, str | None] | None: ...

    def remember(self, url: str, site: str, result: FetchResult) -> None: ...


class MemoryPageCache:
    def __init__(self) -> None:
        self._v: dict[str, tuple[str | None, str | None]] = {}

    def validators(self, url: str) -> tuple[str | None, str | None] | None:
        return self._v.get(url)

    def remember(self, url: str, site: str, result: FetchResult) -> None:
        if result.status == 200 and (result.etag or result.last_modified):
            self._v[url] = (result.etag, result.last_modified)


@dataclass
class CrawlBudget:
    max_requests: int = 500
    max_seconds: float = 600.0
    clock: Callable[[], float] = field(default=time.monotonic)
    used: int = field(default=0, init=False)
    _started: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._started = self.clock()

    def spend(self) -> None:
        with self._lock:
            if self.used >= self.max_requests or self.clock() - self._started >= self.max_seconds:
                raise BudgetExhausted(f"{self.used} requests")
            self.used += 1


def on_site(host: str, site: str) -> bool:
    return host == site or host.endswith("." + site)


@dataclass
class _Robots:
    parser: RobotFileParser
    fetched_at: float
    ttl: float


class PoliteFetcher:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        public_host: Callable[[str], bool] = is_public_host,
        cache: PageCache | None = None,
        budget: CrawlBudget | None = None,
        min_interval: float = 2.0,
        max_bytes: int = MAX_BYTES,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = client(
            "",
            transport,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en;q=0.8"},
        )
        self._public_host, self.cache, self.budget = public_host, cache or MemoryPageCache(), budget or CrawlBudget()
        self.min_interval, self.max_bytes = min_interval, max_bytes
        self._clock, self._sleep = clock, sleep
        self._robots: dict[str, _Robots] = {}
        self._last: dict[str, float] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._host_locks: dict[str, threading.RLock] = {}
        self._lock = threading.Lock()
        self.stats: dict[str, int] = {}

    # ------------------------------------------------------------------ public
    def fetch(self, url: str, *, site: str, accept: tuple[str, ...] = HTML_TYPES) -> FetchResult:
        """GET `url`, which must be on `site`. Returns the page (any status), or raises Blocked (we chose not to),
        RetryableProviderError (try this site later) or BudgetExhausted (this run is done)."""
        for _ in range(MAX_REDIRECTS + 1):
            host = self._check_url(url, site)
            if not self.robots(host, urlsplit(url).scheme, site).can_fetch(BOT_TOKEN, url):
                self._count("blocked_robots")
                raise Blocked("robots", url)
            result = self._get(url, host, accept)
            if result.status in REDIRECTS:  # follow it; every hop is re-checked (same site, public, robots)
                url = result.url
                continue
            return result
        self._count("blocked_redirects")
        raise Blocked("too_many_redirects", url)

    def robots(self, host: str, scheme: str = "https", site: str | None = None) -> RobotFileParser:
        with self._lock:
            cached = self._robots.get(host)
        if cached is not None and self._clock() - cached.fetched_at < cached.ttl:
            return cached.parser
        rp, ttl = RobotFileParser(), ROBOTS_TTL
        try:
            res = self._raw(
                f"{scheme}://{host}/robots.txt", host, ("text/plain", *HTML_TYPES), follow_site=site or host
            )
            if res.status == 200:
                rp.parse(res.text.splitlines())
            elif res.status in (401, 403):  # the site refuses even its rules: stay away (stricter than RFC 9309)
                rp.parse(["User-agent: *", "Disallow: /"])
            elif 400 <= res.status < 500:  # no robots.txt: no rules (RFC 9309 §2.3.1.3)
                rp.parse([])
            else:  # 5xx: assume complete disallow for now (RFC 9309 §2.3.1.4)
                rp.parse(["User-agent: *", "Disallow: /"])
                ttl = ROBOTS_ERROR_TTL
        except (RetryableProviderError, httpx.HTTPError, Blocked):
            rp.parse(["User-agent: *", "Disallow: /"])  # unreachable: same as 5xx
            ttl = ROBOTS_ERROR_TTL
        with self._lock:
            self._robots[host] = _Robots(rp, self._clock(), ttl)
        return rp

    def sitemaps(self, host: str) -> list[str]:
        return list(self.robots(host).site_maps() or [])

    # ------------------------------------------------------------------ internals
    def _check_url(self, url: str, site: str) -> str:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("https", "http") or parts.port not in (None, 80, 443) or parts.username:
            self._count("blocked_url")
            raise Blocked("unsupported_url", url)
        if not on_site(host, site):
            self._count("blocked_off_site")
            raise Blocked("off_site", url)
        if not self._public_host(host):
            self._count("blocked_private")
            raise Blocked("not_public", url)
        return host

    def _breaker(self, host: str) -> CircuitBreaker:
        with self._lock:
            if host not in self._breakers:
                self._breakers[host] = CircuitBreaker(f"host:{host}", threshold=3, cooldown=300.0, clock=self._clock)
            return self._breakers[host]

    def _pace(self, host: str) -> None:
        """One request per host per interval. Called with that host's lock held, so threads crawling different
        hosts never wait for each other."""
        rules = self._robots.get(host)
        delay = rules.parser.crawl_delay(BOT_TOKEN) if rules else None
        interval = min(max(self.min_interval, float(delay or 0)), MAX_CRAWL_DELAY)
        last = self._last.get(host)
        if last is not None:
            wait = last + interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last[host] = self._clock()

    def _host_lock(self, host: str) -> threading.RLock:
        with self._lock:
            return self._host_locks.setdefault(host, threading.RLock())

    def _get(self, url: str, host: str, accept: tuple[str, ...]) -> FetchResult:
        headers = {"Accept": ", ".join(accept) + ";q=0.9, */*;q=0.1"}
        known = self.cache.validators(url)
        if known:
            if known[0]:
                headers["If-None-Match"] = known[0]
            if known[1]:
                headers["If-Modified-Since"] = known[1]
        res = self._raw(url, host, accept, headers=headers)
        if res.status not in REDIRECTS:
            self.cache.remember(url, host, res)
        return res

    def _raw(
        self,
        url: str,
        host: str,
        accept: tuple[str, ...],
        *,
        headers: dict[str, str] | None = None,
        follow_site: str | None = None,
        hops: int = 0,
    ) -> FetchResult:
        breaker = self._breaker(host)
        if not breaker.allow():
            self._count("circuit_open")
            raise RetryableProviderError(f"{host}: circuit open after repeated failures")
        self.budget.spend()
        with self._host_lock(host):
            self._pace(host)
            self._count("requests")
            try:
                with self._http.stream("GET", url, headers=headers or {}) as resp:
                    if resp.status_code in REDIRECTS and resp.headers.get("location"):  # (httpx counts 304)
                        target = urljoin(url, resp.headers.get("location", ""))
                        breaker.success()
                        if follow_site is not None and hops < MAX_REDIRECTS:  # robots.txt: follow on-site only
                            nxt = self._check_url(target, follow_site)
                            return self._raw(target, nxt, accept, follow_site=follow_site, hops=hops + 1)
                        return FetchResult(target, resp.status_code, "", "")
                    if resp.status_code in {429, 503}:
                        breaker.failure()
                        self._count(f"http_{resp.status_code}")
                        raise RateLimited(resp.headers.get("Retry-After", "") or f"{resp.status_code} from {host}")
                    if resp.status_code >= 500:
                        breaker.failure()
                        self._count("http_5xx")
                        raise RetryableProviderError(f"{resp.status_code} from {host}")
                    breaker.success()
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    meta = {"etag": resp.headers.get("etag"), "last_modified": resp.headers.get("last-modified")}
                    if resp.status_code == 304:
                        self._count("not_modified")
                        return FetchResult(url, 304, ctype, "", not_modified=True, **meta)
                    if resp.status_code != 200 or (ctype and not ctype.startswith(accept)):
                        self._count("skipped_status_or_type")
                        return FetchResult(str(resp.url), resp.status_code, ctype, "", **meta)
                    body, truncated = bytearray(), False
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) > self.max_bytes:
                            truncated = True
                            break
                    self._count("pages")
                    text = bytes(body[: self.max_bytes]).decode(resp.encoding or "utf-8", "replace")
                    return FetchResult(str(resp.url), 200, ctype, text, truncated=truncated, **meta)
            except RetryableProviderError:
                raise
            except httpx.HTTPError as exc:  # unreachable, or a read failing mid-flight: try this site later
                breaker.failure()
                self._count("network_errors")
                raise RetryableProviderError(f"{type(exc).__name__} from {host}") from exc

    def _count(self, key: str) -> None:
        with self._lock:
            self.stats[key] = self.stats.get(key, 0) + 1
