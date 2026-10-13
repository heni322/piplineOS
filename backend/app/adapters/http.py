"""Shared HTTP plumbing for real adapters: strict timeouts, an injectable transport (tests use
httpx.MockTransport, so no network is ever touched), and one mapping from HTTP status to our error types."""

from __future__ import annotations

from typing import Any

import httpx

from app.adapters.base import RateLimited, RetryableProviderError

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class ProviderError(Exception):
    """Permanent provider error (4xx other than 429). Not retried."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail[:300]}")
        self.status = status


def client(base_url: str, transport: httpx.BaseTransport | None = None, **kw: Any) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=DEFAULT_TIMEOUT, transport=transport, follow_redirects=False, **kw)


def check(resp: httpx.Response) -> httpx.Response:
    if resp.status_code == 429:
        raise RateLimited(resp.headers.get("Retry-After", "rate limited"))
    if resp.status_code >= 500:
        raise RetryableProviderError(f"{resp.status_code} from provider")
    if resp.status_code >= 400:
        raise ProviderError(resp.status_code, resp.text)
    return resp
