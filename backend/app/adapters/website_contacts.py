"""Free, published business contact addresses from the company's own website.

For a decision-maker found on the register (no email address), this finds the company's website and takes the
generic business address the company itself publishes for enquiries (sales@, enquiries@, hello@, info@...). The
message is then addressed to the named director at that published address.

1. **The website.** The domain we already hold; else Wikidata's "official website" for the Companies House number
   (CC0 open data); else a few names derived from the company name (name.co.uk, name.com...).
2. **Proof it is theirs.** UK companies must show their registered number on their website (Companies (Trading
   Disclosures) Regulations 2008). A site is accepted only when one of its pages states the company's number.
   A name match alone is never enough.
3. **The address.** Only generic role addresses on that same domain, found in the pages we read. Personal addresses
   (firstname@, first.last@) are never taken, and nor are privacy, careers, accounts or no-reply mailboxes.
4. **Checked:** the domain has a mail server (MX). An address published on the company's own verified site with a
   working mail domain is reported "valid" by `verify`; anything else stays "unknown".

Polite and safe: robots.txt is respected; at most a handful of small pages per company; only public internet
addresses are contacted (no private, loopback or link-local IPs); redirects only within the same domain.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass, replace
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.adapters.base import RetryableProviderError
from app.adapters.companies_house.live import normalise_name
from app.adapters.enrichment.base import EnrichedPerson, EnrichmentProvider
from app.adapters.signals.ocds import normalise_company_number
from app.adapters.verifier.base import Verification
from app.core.logging import get_logger

log = get_logger(__name__)
USER_AGENT = "PipelineOS-contact-check/1.0 (published business contacts; respects robots.txt)"
PAGES = ["/", "/contact", "/contact-us", "/about", "/about-us", "/privacy-policy", "/terms"]
MAX_BYTES = 512_000
MAX_CANDIDATES = 6
# preferred first; anything else is ignored
GENERIC = ["sales", "enquiries", "enquiry", "hello", "contact", "info", "office", "newbusiness", "business", "team"]
# bounded (RFC 5321 limits): an unbounded local part makes a long run of letters quadratic to scan (ReDoS)
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}")
_TAGS = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
WIKIDATA = "https://query.wikidata.org"


def is_public_host(host: str) -> bool:
    """True only when every address the host resolves to is a public internet address."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    addrs = {ipaddress.ip_address(i[4][0]) for i in infos}
    return bool(addrs) and all(a.is_global for a in addrs)


def has_mx(domain: str) -> bool:
    import dns.exception
    import dns.resolver

    try:
        return len(dns.resolver.resolve(domain, "MX", lifetime=5.0)) > 0
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        return False


def candidate_domains(company_name: str) -> list[str]:
    """ "Lakeside Networks Ltd" -> lakesidenetworks.co.uk, lakeside-networks.co.uk, lakesidenetworks.com, ..."""
    words = [w for w in normalise_name(company_name).split() if w.isalnum()]
    if not words:
        return []
    stems = list(dict.fromkeys(["".join(words), "-".join(words)]))
    out = [f"{s}.{tld}" for tld in ("co.uk", "com", "uk") for s in stems]
    return [d for d in out if _DOMAIN.match(d)][:MAX_CANDIDATES]


def page_text(html: str) -> str:
    return re.sub(r"\s+", " ", _TAGS.sub(" ", html))


def generic_addresses(html: str, domain: str) -> list[str]:
    """Generic role addresses on `domain` (or a subdomain), in preference order."""
    found = {m.group(0).lower().rstrip(".") for m in _EMAIL.finditer(html.replace("%40", "@"))}
    by_local: dict[str, str] = {}
    for e in found:
        local, _, host = e.partition("@")
        if host == domain or host.endswith("." + domain):
            by_local.setdefault(local, e)
    return [by_local[g] for g in GENERIC if g in by_local]


def states_number(text: str, number: str) -> bool:
    """The page states this company number (as printed: 01234567, or without leading zeros: 1234567)."""
    forms = {number, number.lstrip("0")} if number.isdigit() else {number}
    return any(re.search(rf"(?<![A-Za-z0-9]){re.escape(f)}(?![A-Za-z0-9])", text) for f in forms if len(f) >= 5)


@dataclass(frozen=True)
class Page:
    url: str
    status_code: int
    content_type: str
    text: str


@dataclass(frozen=True)
class SiteContact:
    domain: str
    address: str | None
    page: str  # where the address (or, without one, the number) was found


class WebsiteFinder:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        wikidata_transport: httpx.BaseTransport | None = None,
        public_host: Callable[[str], bool] = is_public_host,
        mx: Callable[[str], bool] = has_mx,
    ) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
        self._http = httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), transport=transport, headers=headers)
        self._wd = httpx.Client(
            base_url=WIKIDATA,
            timeout=httpx.Timeout(15.0, connect=5.0),
            transport=wikidata_transport,
            headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
        )
        self._public_host, self._mx = public_host, mx

    def wikidata_domains(self, number: str) -> list[str]:
        q = f'SELECT ?w WHERE {{ ?c wdt:P2622 "{number}"; wdt:P856 ?w . }} LIMIT 3'
        try:
            resp = self._wd.get("/sparql", params={"query": q, "format": "json"})
            rows = resp.json()["results"]["bindings"] if resp.status_code == 200 else []
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return []
        hosts = [urlsplit(str(r.get("w", {}).get("value", ""))).hostname or "" for r in rows]
        return [h.lower().removeprefix("www.") for h in hosts if h]

    def _get(self, url: str, domain: str) -> Page | None:
        """GET with up to 3 redirects, each only within `domain` and to a public address. At most MAX_BYTES of the
        body are read (streamed), whatever the server sends."""
        for _ in range(4):
            host = urlsplit(url).hostname or ""
            if not (host == domain or host.endswith("." + domain)) or not self._public_host(host):
                return None
            try:
                with self._http.stream("GET", url, follow_redirects=False) as resp:
                    if resp.is_redirect and resp.headers.get("location"):
                        url = urljoin(url, resp.headers["location"])
                        continue
                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) >= MAX_BYTES:
                            break
                    text = bytes(body[:MAX_BYTES]).decode(resp.encoding or "utf-8", "replace")
                    return Page(str(resp.url), resp.status_code, resp.headers.get("content-type", ""), text)
            except httpx.HTTPError:
                return None
        return None

    def _robots(self, base: str, domain: str) -> RobotFileParser:
        rp = RobotFileParser()
        resp = self._get(base + "/robots.txt", domain)
        if resp is not None and resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        elif resp is not None and resp.status_code in (401, 403):
            rp.parse(["User-agent: *", "Disallow: /"])  # the site refuses robots.txt itself: stay away
        else:
            rp.parse([])  # no robots.txt: everything allowed (parse also marks it as read)
        return rp

    def check_site(self, domain: str, number: str) -> SiteContact | None:
        """The site, when one of its pages states `number`; with the best generic address found, if any."""
        if not _DOMAIN.match(domain):
            return None
        base = f"https://{domain}"
        robots = self._robots(base, domain)
        verified_at: str | None = None
        addresses: dict[str, str] = {}
        for path in PAGES:
            url = base + path
            if not robots.can_fetch(USER_AGENT, url):
                continue
            resp = self._get(url, domain)
            if resp is None or resp.status_code != 200 or "html" not in (resp.content_type or "html"):
                continue
            html = resp.text
            if verified_at is None and states_number(page_text(html), number):
                verified_at = str(resp.url)
            for a in generic_addresses(html, domain):
                addresses.setdefault(a, str(resp.url))
            if verified_at and addresses:
                break
        if verified_at is None:
            return None
        best = next((a for g in GENERIC for a in addresses if a.split("@", 1)[0] == g), None)
        if best is not None and not self._mx(best.split("@", 1)[1]):
            best = None
        return SiteContact(domain, best, addresses.get(best, verified_at) if best else verified_at)

    def find(self, number: str, company_name: str | None, known_domain: str | None) -> SiteContact | None:
        if known_domain:
            candidates = [known_domain.lower().removeprefix("www.")]
        else:
            candidates = self.wikidata_domains(number) + candidate_domains(company_name or "")
        for d in list(dict.fromkeys(candidates))[: MAX_CANDIDATES + 3]:
            site = self.check_site(d, number)
            if site is not None:
                return site
        return None


Lookup = Callable[[str, str], list[str]]  # (name, "MX" | "A") -> records; same shape as dns_check.dns_lookup()


def mail_domain_status(domain: str, lookup: Lookup) -> Verification:
    """Can this domain receive email at all? Free (public DNS), and it catches the commonest hard bounce: an address
    on a domain that no longer takes mail. MX present -> valid; null MX (RFC 7505) -> invalid; no MX but an A/AAAA
    record (implicit MX, RFC 5321) -> risky, which the compliance gate does not send to; nothing -> invalid; the
    resolver failing (the lookup raises) -> RetryableProviderError: the lead waits and is retried, so a DNS outage never
    condemns a good address."""
    try:
        mx = lookup(domain, "MX")
        if mx:
            return "invalid" if all(not h for h in mx) else "valid"
        return "risky" if lookup(domain, "A") or lookup(domain, "AAAA") else "invalid"
    except Exception as exc:  # resolver unavailable: decide nothing now; the lead is retried on the next run
        raise RetryableProviderError(f"dns unavailable for {domain}") from exc


class WebsiteContacts:
    """Enrichment (register people + the company's published address) and verifier in one: `verify` says "valid"
    only for an address this object found published on the company's own verified website, on a domain that
    accepts email (MX check, when a DNS lookup is given)."""

    name = "website.published_contacts"

    def __init__(self, people: EnrichmentProvider, finder: WebsiteFinder, lookup: Lookup | None = None) -> None:
        self._people, self._finder, self._lookup = people, finder, lookup
        self._published: set[str] = set()
        self._domain_status: dict[str, Verification] = {}  # one DNS check per domain, not per address

    def find_people(
        self,
        company_domain: str,
        persona_titles: list[str],
        company_name: str | None = None,
        *,
        company_number: str | None = None,
    ) -> list[EnrichedPerson]:
        people = self._people.find_people(company_domain, persona_titles, company_name, company_number=company_number)
        number = normalise_company_number(company_number or "") or _number_from(people)
        if not people or number is None:
            return people
        site = self._finder.find(number, company_name, company_domain or None)
        if site is None:
            return people
        if site.address:
            self._published.add(site.address.lower())
        return [
            replace(
                p,
                company_domain=site.domain,
                professional_email=site.address,
                data_source=f"{p.data_source}; email published at {site.page}" if site.address else p.data_source,
            )
            for p in people
        ]

    def verify(self, email: str) -> Verification:
        email = email.lower()
        if email not in self._published:
            return "unknown"
        if self._lookup is None:
            return "valid"
        domain = email.rsplit("@", 1)[1]
        if domain not in self._domain_status:  # an outage raises before caching, so it is retried next time
            self._domain_status[domain] = mail_domain_status(domain, self._lookup)
        return self._domain_status[domain]


def _number_from(people: list[EnrichedPerson]) -> str | None:
    for p in people:
        if p.data_source.startswith("companies_house:officers:"):
            return normalise_company_number(p.data_source.rsplit(":", 1)[1])
    return None
