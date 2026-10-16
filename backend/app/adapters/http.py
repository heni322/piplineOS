"""Shared HTTP plumbing for real adapters: strict timeouts, an injectable transport (tests use
httpx.MockTransport, so no network is ever touched), and one mapping from failures to our error types.

Failure taxonomy (ADR-0019). Every adapter's caller decides from the error TYPE alone:
- RetryableProviderError: the provider certainly did nothing (429, 503, connection refused, DNS failure, connect
  timeout; or any network failure on a read-only request). Callers defer the work and retry later.
- ProviderError: a definite, permanent refusal (4xx other than 429). Not retried.
- anything else from a non-idempotent request (e.g. a read timeout after an email was handed over): the outcome is
  UNKNOWN. Callers must never retry it automatically; the send path routes it to human reconciliation."""

from __future__ import annotations

from typing import Any

import httpx

from app.adapters.base import RateLimited, RetryableProviderError

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# The request never left this machine, or never reached the provider: retrying cannot cause a duplicate.
_NOT_DELIVERED = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.UnsupportedProtocol)


class ProviderError(Exception):
    """Permanent provider error (4xx other than 429). Not retried."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail[:300]}")
        self.status = status


class ProviderUnreachable(RetryableProviderError, httpx.TransportError):
    """The provider could not be reached, or a read-only request failed in transit. Safe to retry.

    Also an httpx.TransportError, so code that already catches httpx.HTTPError keeps working unchanged."""

    def __init__(self, message: str, *, request: httpx.Request | None = None) -> None:
        httpx.TransportError.__init__(self, message, request=request)


class AmbiguousSendError(Exception):
    """A non-idempotent request may or may not have been carried out (5xx other than 503). Never retried
    automatically: the outbound gateway leaves the message for human reconciliation."""


class _ClassifyingClient(httpx.Client):
    def send(self, request: httpx.Request, **kw: Any) -> httpx.Response:
        try:
            return super().send(request, **kw)
        except _NOT_DELIVERED as exc:
            raise ProviderUnreachable(f"{type(exc).__name__} to {request.url.host}", request=request) from exc
        except httpx.TransportError as exc:
            if request.method in SAFE_METHODS:  # a read can always be repeated
                raise ProviderUnreachable(f"{type(exc).__name__} from {request.url.host}", request=request) from exc
            raise  # a write may have happened: the outcome is unknown, so it is NOT made retryable


def client(
    base_url: str,
    transport: httpx.BaseTransport | None = None,
    *,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    **kw: Any,
) -> httpx.Client:
    return _ClassifyingClient(base_url=base_url, timeout=timeout, transport=transport, follow_redirects=False, **kw)


def check(resp: httpx.Response) -> httpx.Response:
    if resp.status_code == 429:
        raise RateLimited(resp.headers.get("Retry-After", "rate limited"))
    if resp.status_code >= 500:
        raise RetryableProviderError(f"{resp.status_code} from provider")
    if resp.status_code >= 400:
        raise ProviderError(resp.status_code, resp.text)
    return resp


def check_send(resp: httpx.Response) -> httpx.Response:
    """check() for a request that must happen at most once (sending an email). Only 429 and 503 mean "refused,
    nothing done"; any other 5xx may have been carried out, so it is ambiguous and never retried."""
    if resp.status_code >= 500 and resp.status_code != 503:
        raise AmbiguousSendError(f"{resp.status_code} from provider: outcome unknown")
    return check(resp)
