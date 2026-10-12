"""Adapter framework: one interface per external service; mock / sandbox / live implementations.

Side-effecting adapters (email, calendar, crm) may ONLY be imported by app.domain.outbound
(enforced by import-linter, see backend/.importlinter).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


class AdapterNotConfigured(Exception):
    """Raised by live/sandbox implementations that are not yet configured (until Phase 6)."""


class RetryableProviderError(Exception):
    """Provider refused BEFORE accepting the request (rate limit, 5xx on connect). Safe to retry with same key."""


class RateLimited(RetryableProviderError):
    pass


@dataclass
class TokenBucket:
    """Simple in-process rate limiter. Production workers use the Valkey-backed limiter (Phase 6)."""

    rate_per_sec: float
    capacity: int
    _tokens: float = field(init=False)
    _last: float = field(init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        self._tokens = float(self.capacity)
        self._last = time.monotonic()

    def try_acquire(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate_per_sec)
            self._last = now
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            return False

    def acquire_or_raise(self) -> None:
        if not self.try_acquire():
            raise RateLimited("local rate limit reached")
