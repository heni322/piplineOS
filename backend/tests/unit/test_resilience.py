"""Fallback mechanisms (ADR-0019), without a database: failure taxonomy, circuit breaker, bulkhead, LLM outage,
bounce classes, send backoff."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy.exc
from pydantic import BaseModel

from app.adapters.base import RateLimited, RetryableProviderError
from app.adapters.http import AmbiguousSendError, ProviderError, ProviderUnreachable, check_send, client
from app.core import ratelimit
from app.core.resilience import TRANSIENT_ERRORS, Bulkhead, CircuitBreaker, is_transient
from app.domain.deliverability.killswitch import classify_bounce
from app.domain.outbound.gateway import BACKOFF_CAP, retry_delay
from app.llm.client import BackendResponse, LLMClient, LLMEscalation, LLMUnavailable
from app.llm.prompts import PromptFile


# ------------------------------------------------------------------------------------------- circuit breaker
def test_breaker_opens_after_repeated_failures_then_lets_one_trial_through_after_the_cooldown() -> None:
    now = [0.0]
    b = CircuitBreaker("x", threshold=3, cooldown=30.0, clock=lambda: now[0])
    for _ in range(2):
        assert b.allow()
        b.failure()
    assert b.state == "closed"
    b.failure()
    assert b.state == "open" and not b.allow()
    now[0] = 31.0
    assert b.state == "half_open"
    assert b.allow()  # one trial
    assert not b.allow()  # and only one
    b.failure()  # trial failed: open again for a full cooldown
    assert not b.allow()
    now[0] = 62.0
    assert b.allow()
    b.success()
    assert b.state == "closed" and b.allow() and b.allow()


def test_bulkhead_keeps_going_when_one_item_fails() -> None:
    def job(n: int) -> int:
        if n == 2:
            raise RuntimeError("poison")
        return n * 10

    bh = Bulkhead("t")
    assert bh.run([1, 2, 3], job) == [10, 30]
    assert bh.failed == 1


def test_transient_errors_cover_what_is_really_raised() -> None:
    # Celery's old autoretry_for=(ConnectionError,) matched none of these, so task retries never fired.
    for exc in (
        httpx.ConnectError("x"),
        httpx.ReadTimeout("x"),
        sqlalchemy.exc.OperationalError("SELECT 1", {}, Exception("server closed the connection")),
        RetryableProviderError("503"),
        RateLimited("30"),
        LLMUnavailable("down"),
    ):
        assert is_transient(exc), type(exc).__name__
    assert not is_transient(ValueError("bug"))
    assert not is_transient(ProviderError(400, "bad request"))
    assert ConnectionError in TRANSIENT_ERRORS


# ------------------------------------------------------------------------------------------- HTTP taxonomy
def _client(handler: Any) -> httpx.Client:
    return client("https://api.example", httpx.MockTransport(handler))


def test_unreachable_provider_is_retryable_and_still_an_httpx_error() -> None:
    def refuse(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    for method in ("GET", "POST"):  # nothing reached the provider: safe for writes too
        with pytest.raises(ProviderUnreachable) as exc:
            _client(refuse).request(method, "/x")
        assert isinstance(exc.value, RetryableProviderError) and isinstance(exc.value, httpx.HTTPError)


def test_a_read_failing_mid_flight_is_retryable_but_a_write_is_not() -> None:
    def timeout(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(ProviderUnreachable):
        _client(timeout).get("/x")
    with pytest.raises(httpx.ReadTimeout) as exc:  # the write may have happened: the outcome is unknown
        _client(timeout).post("/x")
    assert not isinstance(exc.value, RetryableProviderError)


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, RateLimited), (503, RetryableProviderError), (500, AmbiguousSendError), (502, AmbiguousSendError)],
)
def test_send_status_classification(status: int, error: type[Exception]) -> None:
    with pytest.raises(error):
        check_send(httpx.Response(status, request=httpx.Request("POST", "https://x.example")))


def test_send_backoff_grows_is_capped_and_honours_retry_after() -> None:
    assert [retry_delay(n) for n in (1, 2, 3)] == [timedelta(minutes=2), timedelta(minutes=4), timedelta(minutes=8)]
    assert retry_delay(12) == BACKOFF_CAP
    assert retry_delay(1, "900") == timedelta(seconds=900)  # provider asked for 15 minutes
    assert retry_delay(1, "soon") == timedelta(minutes=2)  # unparseable Retry-After: ignored


# ------------------------------------------------------------------------------------------- LLM outage
class _Out(BaseModel):
    ok: bool


PROMPT = PromptFile(
    id="t", version=1, model_tier="volume", max_tokens=50, budget_micros_gbp=None, body="x", sha256="0", path=Path("t")
)


class _Down:
    calls = 0

    def complete(self, **_: Any) -> BackendResponse:
        _Down.calls += 1
        raise httpx.ConnectError("ollama not running")


def _llm(backend: Any) -> LLMClient:
    return LLMClient(backend=backend, models={"volume": "m"}, recorder=lambda r: None, sleep=lambda s: None)


def _run(c: LLMClient) -> _Out:
    return c.run_structured(prompt=PROMPT, schema=_Out, user_content="u", tenant_id=uuid.uuid4(), agent="t")


def test_model_outage_is_unavailable_not_bad_output_and_the_circuit_then_fails_fast() -> None:
    _Down.calls = 0
    c = _llm(_Down())
    for _ in range(3):
        with pytest.raises(LLMUnavailable) as exc:
            _run(c)
        assert isinstance(exc.value, LLMEscalation)  # callers that cannot wait fall back exactly as before
        assert exc.value.reason == "provider_unavailable"
    with pytest.raises(LLMUnavailable, match="circuit open"):
        _run(c)
    assert _Down.calls == 3  # the 4th call never reached the model


def test_a_test_double_running_out_is_a_bug_not_an_outage() -> None:
    from app.llm.backends import MockBackend

    with pytest.raises(AssertionError):
        _run(_llm(MockBackend([])))


# ------------------------------------------------------------------------------------------- bounces
@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("550 5.1.1 <sam@contoso.example>: Recipient address rejected: User unknown", "hard"),
        ("550 5.2.1 The email account that you tried to reach is disabled", "hard"),
        ("550 5.7.1 Service unavailable; client host blocked using Spamhaus", "policy"),
        ("550 5.7.26 Unauthenticated email is not accepted (DMARC)", "policy"),
        ("552 5.2.2 Mailbox full: mailbox unavailable", "policy"),
        ("451 4.7.1 Greylisted, please try again later", "soft"),
        ("Your message to sam@contoso.example couldn't be delivered: user unknown", "hard"),
        ("Delivery is delayed. We'll keep trying.", "soft"),
    ],
)
def test_bounce_classes(body: str, kind: str) -> None:
    assert classify_bounce(body) == kind


# ------------------------------------------------------------------------------------------- Valkey breaker
def test_rate_limiter_stops_waiting_on_valkey_once_it_is_known_to_be_down(monkeypatch: pytest.MonkeyPatch) -> None:
    ratelimit.reset_local()
    calls = []

    class _Dead:
        def eval(self, *a: Any) -> Any:
            calls.append(1)
            raise ConnectionError("valkey down")

    monkeypatch.setattr(ratelimit, "_client", lambda url: _Dead())
    for _ in range(5):
        assert ratelimit.allow("k", rate_per_sec=10, capacity=10, redis_url="redis://valkey:6379/0")
    assert len(calls) == 2  # after two failures the local bucket answers alone (no socket timeout per request)
    ratelimit.reset_local()
