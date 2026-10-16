"""Structure from public pages, robust to redesigns (ADR-0020).

We read what sites publish FOR machines, not their visual layout:
- schema.org JSON-LD (the format Google for Jobs requires for job ads, so employers keep it accurate);
- links, by where they point and what they say ("Careers", "Vacancies", "Join us"), never by CSS class;
- XML sitemaps (parsed with defusedxml: no entities, no external references).

Everything returned is UNTRUSTED data, bounded in size and count.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from app.scraping.fetcher import on_site

MAX_JSONLD_BLOCKS = 50
MAX_JSONLD_BYTES = 300_000
MAX_LINKS = 2000
MAX_SITEMAP_URLS = 5000
CAREER_WORDS = re.compile(
    r"(?<![a-z])(careers?|jobs?|vacanc(?:y|ies)|join[ -]?(?:us|our[ -]team)|work[ -](?:with|for)[ -]us|"
    r"recruit(?:ment|ing)?|opportunities|current[ -]openings)(?![a-z])",
    re.IGNORECASE,
)
# Hosted job boards we can read through their free public APIs (app.adapters.signals.job_boards)
ATS_LINK = re.compile(
    r"^https://(?:"
    r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?P<gh>[A-Za-z0-9_.-]{1,100})"
    r"|jobs\.(?P<eu>eu\.)?lever\.co/(?P<lv>[A-Za-z0-9_.-]{1,100})"
    r"|jobs\.ashbyhq\.com/(?P<ab>[A-Za-z0-9_.-]{1,100})"
    r")(?:[/?#]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Link:
    url: str
    text: str


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[Link] = []
        self.jsonld: list[str] = []
        self.canonical: str | None = None
        self._in_ld = False
        self._ld: list[str] = []
        self._ld_size = 0
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and a.get("type", "").split(";")[0].strip().lower() == "application/ld+json":
            if len(self.jsonld) < MAX_JSONLD_BLOCKS:
                self._in_ld, self._ld, self._ld_size = True, [], 0
        elif tag == "a" and a.get("href") and len(self.links) < MAX_LINKS:
            self._href, self._text = a["href"], [a.get("title", ""), a.get("aria-label", "")]
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split() and a.get("href"):
            self.canonical = a["href"]

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self._ld_size += len(data)
            if self._ld_size <= MAX_JSONLD_BYTES:
                self._ld.append(data)
        elif self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_ld:
            self._in_ld = False
            if self._ld_size <= MAX_JSONLD_BYTES:
                self.jsonld.append("".join(self._ld))
        elif tag == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", " ".join(self._text)).strip()[:200]
            self.links.append(Link(self._href, text))
            self._href = None


@dataclass(frozen=True)
class Parsed:
    links: list[Link]
    jsonld: list[dict[str, Any]]
    canonical: str | None
    json_errors: int


def parse_html(html: str, base_url: str) -> Parsed:
    p = _Parser()
    try:
        p.feed(html)
        p.close()
    except (AssertionError, ValueError):  # malformed markup: keep what was parsed so far
        pass
    objs: list[dict[str, Any]] = []
    errors = 0
    for raw in p.jsonld:
        try:
            data = json.loads(_clean_json(raw))
        except ValueError:
            errors += 1
            continue
        objs.extend(_flatten(data))
    links = [Link(_absolute(link.url, base_url), link.text) for link in p.links]
    return Parsed(
        [link for link in links if link.url], objs, _absolute(p.canonical, base_url) if p.canonical else None, errors
    )


def _clean_json(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"^\s*(?://\s*)?<!\[CDATA\[|(?://\s*)?\]\]>\s*$", "", s)  # CDATA wrappers some CMSs add
    s = re.sub(r"^\s*<!--|-->\s*$", "", s)
    return s.strip()


def _flatten(data: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Every object in a JSON-LD document: top-level lists, @graph containers, one level of nesting is enough."""
    if depth > 3:
        return
    if isinstance(data, list):
        for item in data[:500]:
            yield from _flatten(item, depth + 1)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _flatten(data["@graph"], depth + 1)
        if "@type" in data:
            yield data
        for key in ("itemListElement", "mainEntity", "item"):
            if key in data:
                yield from _flatten(data[key], depth + 1)


def types_of(obj: dict[str, Any]) -> set[str]:
    t = obj.get("@type")
    values = t if isinstance(t, list) else [t]
    return {str(v).rsplit("/", 1)[-1] for v in values if v}


def of_type(objs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [o for o in objs if name in types_of(o)]


def _absolute(href: str, base: str) -> str:
    href = href.strip()
    if not href or href.startswith(("mailto:", "tel:", "javascript:", "data:", "#")):
        return ""
    parts = urlsplit(urljoin(base, href))
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))  # no fragment


def career_links(links: list[Link], site: str) -> list[str]:
    """On-site links that lead to job ads, best first: matched in the address and the link text."""
    scored: dict[str, int] = {}
    for link in links:
        host = (urlsplit(link.url).hostname or "").lower()
        if not on_site(host, site):
            continue
        path = urlsplit(link.url).path
        score = (2 if CAREER_WORDS.search(path.replace("/", " ")) else 0) + (1 if CAREER_WORDS.search(link.text) else 0)
        if score:
            scored[link.url] = max(score, scored.get(link.url, 0))
    return sorted(scored, key=lambda u: (-scored[u], len(u)))


def ats_board(links: list[Link]) -> str | None:
    """A hosted job board the careers page links to, in our "<ats>:<board>" form, if we can read it for free."""
    for link in links:
        m = ATS_LINK.match(link.url)
        if not m:
            continue
        if m.group("gh"):
            return f"greenhouse:{m.group('gh').lower()}"
        if m.group("lv"):
            return f"{'lever-eu' if m.group('eu') else 'lever'}:{m.group('lv').lower()}"
        if m.group("ab"):
            return f"ashby:{m.group('ab').lower()}"
    return None


@dataclass(frozen=True)
class Sitemap:
    pages: list[str]
    children: list[str]  # nested sitemaps (a sitemap index)


def parse_sitemap(xml: str, site: str) -> Sitemap:
    try:
        root = fromstring(xml.encode("utf-8", "replace"))
    except (ParseError, DefusedXmlException, ValueError):
        return Sitemap([], [])
    pages: list[str] = []
    children: list[str] = []
    for el in root.iter():
        if not el.tag.endswith("loc") or not el.text:
            continue
        url = _absolute(el.text, f"https://{site}/")
        if not url or not on_site((urlsplit(url).hostname or "").lower(), site):
            continue
        (children if root.tag.endswith("sitemapindex") else pages).append(url)
        if len(pages) + len(children) >= MAX_SITEMAP_URLS:
            break
    return Sitemap(pages, children)


def career_pages_in(urls: list[str]) -> list[str]:
    return sorted({u for u in urls if CAREER_WORDS.search(urlsplit(u).path.replace("/", " "))}, key=len)
