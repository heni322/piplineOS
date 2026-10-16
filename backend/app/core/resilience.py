"""Resilience primitives (ADR-0019): which failures are transient, a circuit breaker, and a per-item bulkhead.

The rule behind all of them: an outage of something we depend on must DEFER work, never decide it. No lead is
rejected, no reply is lost, and no email is sent twice because a provider was briefly unreachable."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TypeVar

from app.adapters.base import RetryableProviderError
from app.core.logging import get_logger

log = get_logger(__name__)
K = TypeVar("K")
R = TypeVar("R")


def _transient() -> tuple[type[BaseException], ...]:
    import httpx
    import kombu.exceptions
    import redis.exceptions
    import sqlalchemy.exc

    return (
        RetryableProviderError,  # includes RateLimited, ProviderUnreachable and LLMUnavailable
        httpx.TransportError,
        sqlalchemy.exc.OperationalError,  # database restarting, failover, connection dropped
        sqlalchemy.exc.InterfaceError,
        kombu.exceptions.OperationalError,  # broker unreachable
        redis.exceptions.ConnectionError,
        redis.exceptions.TimeoutError,
        ConnectionError,
        TimeoutError,
    )


# For Celery's autoretry_for. The builtin ConnectionError alone matches none of httpx, SQLAlchemy, kombu or the
# LLM errors, so before this tuple the task retries never fired.
TRANSIENT_ERRORS: tuple[type[BaseException], ...] = _transient()


def is_transient(exc: BaseException) -> bool:
    return isinstance(exc, TRANSIENT_ERRORS)


@dataclass
class CircuitBreaker:
    """Stops calling a dependency that keeps failing, so an outage costs one timeout per cooldown instead of one per
    request. Closed -> (threshold consecutive failures) -> open for `cooldown` seconds -> half-open: one trial
    call; success closes it, failure re-opens it. Per process and thread-safe."""

    name: str
    threshold: int = 3
    cooldown: float = 30.0
    clock: Callable[[], float] = field(default=time.monotonic)
    _failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)
    _trial: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            return "half_open" if self.clock() - self._opened_at >= self.cooldown else "open"

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if self.clock() - self._opened_at < self.cooldown or self._trial:
                return False
            self._trial = True  # exactly one trial call while half-open
            return True

    def success(self) -> None:
        with self._lock:
            if self._opened_at is not None:
                log.info("circuit_closed", breaker=self.name)
            self._failures, self._opened_at, self._trial = 0, None, False

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._trial = False
            if self._opened_at is not None or self._failures >= self.threshold:
                if self._opened_at is None:
                    log.warning("circuit_opened", breaker=self.name, failures=self._failures)
                self._opened_at = self.clock()

    def reset(self) -> None:  # tests
        self.success()


@dataclass
class Bulkhead:
    """Runs one job per item and keeps going when one fails: a single tenant, mailbox or message can never stop
    the others. Failures are logged by type only (no PII) and counted."""

    job: str
    failed: int = 0

    def run(self, items: Iterable[K], fn: Callable[[K], R]) -> list[R]:
        out: list[R] = []
        for item in items:
            try:
                out.append(fn(item))
            except Exception as exc:
                self.failed += 1
                log.error("bulkhead_item_failed", job=self.job, item=str(item), error=type(exc).__name__)
        return out
