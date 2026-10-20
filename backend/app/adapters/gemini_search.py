"""Web research with Gemini and Grounding with Google Search: the operator asks a question about a company ("What is
the official website of Exemple Infogérance SAS, Lyon?") and reads the answer with its sources.

Paid: Google bills each search query the model runs (paid tier: 5,000 free a month across Gemini 3 models, then
$14 per 1,000; list price read 2026-10-19), so only app.domain.paid builds this (import contract `paid-only`), behind
the three-key switch and a monthly cap. On the paid tier Google does not use prompts to improve its products.

Google's terms for grounded results shape the design: they may only be shown to the person who asked, together with
Google's Search Suggestions, unchanged; they may not be cached, analysed or used for anything else. So the answer is
returned to the operator's screen and never stored, logged or fed into the pipeline; the operator decides what to do
with it (for example, add a company to a watchlist by hand).

API: POST /v1beta/models/{model}:generateContent with tools [{"google_search": {}}], key in the x-goog-api-key header.
The response's groundingMetadata gives the sources (groundingChunks[].web), the queries run (webSearchQueries) and the
Search Suggestions to display (searchEntryPoint.renderedContent). Check the shape once on first live use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.adapters.base import RateLimited, RetryableProviderError
from app.adapters.http import ProviderError

GEMINI_URL = "https://generativelanguage.googleapis.com"
INSTRUCTION = (
    "You help a B2B sales operator research companies. Answer briefly and factually from the search results: the "
    "company's official website, what it does, its location and its register number if published. Say plainly when "
    "you are not sure. Do not give information about private individuals."
)


@dataclass(frozen=True)
class Source:
    title: str
    url: str


@dataclass
class GroundedAnswer:
    text: str
    sources: list[Source] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    suggestions_html: str = ""  # Google's Search Suggestions, to be displayed unchanged next to the answer


def _check(resp: httpx.Response) -> httpx.Response:
    if resp.status_code == 429:
        raise RateLimited(resp.headers.get("Retry-After", "gemini rate limit"))
    if resp.status_code >= 500:
        raise RetryableProviderError(f"{resp.status_code} from Gemini")
    if resp.status_code >= 400:
        raise ProviderError(resp.status_code, resp.text[:300])
    return resp


def parse(data: dict[str, Any]) -> GroundedAnswer:
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(
        str(p.get("text", "")) for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought")
    ).strip()
    meta = cand.get("groundingMetadata") or {}
    sources: list[Source] = []
    for chunk in meta.get("groundingChunks") or []:
        web = chunk.get("web") or {}
        url = str(web.get("uri") or "")
        if urlsplit(url).scheme in ("http", "https") and all(s.url != url for s in sources):
            sources.append(Source(str(web.get("title") or urlsplit(url).hostname or url)[:200], url))
    return GroundedAnswer(
        text=text,
        sources=sources,
        queries=[str(q) for q in meta.get("webSearchQueries") or []],
        suggestions_html=str((meta.get("searchEntryPoint") or {}).get("renderedContent") or ""),
    )


class GeminiSearch:
    name = "gemini.search"

    def __init__(
        self, api_key: str, model: str, *, base_url: str = GEMINI_URL, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not api_key:
            raise ValueError("Gemini API key missing")
        self.model = model
        self._http = httpx.Client(
            base_url=base_url,
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(60.0, connect=10.0),
            headers={"x-goog-api-key": api_key},
        )

    def ask(self, question: str) -> GroundedAnswer:
        body = {
            "systemInstruction": {"parts": [{"text": INSTRUCTION}]},
            "contents": [{"role": "user", "parts": [{"text": question[:1000]}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 1024},
        }
        try:
            resp = self._http.post(f"/v1beta/models/{self.model}:generateContent", json=body)
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("Gemini timed out") from exc
        return parse(_check(resp).json())
