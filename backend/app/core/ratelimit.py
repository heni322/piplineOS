"""Rate limiting shared by every API worker: a token bucket in Valkey (atomic Lua). If Valkey is unreachable the
limiter falls back to a per-process bucket, so limits get looser (per process) but never disappear."""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from app.adapters.base import TokenBucket
from app.core.logging import get_logger

log = get_logger(__name__)
_LUA = """
local key, rate, cap, now = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
local s = redis.call('HMGET', key, 't', 'ts')
local tokens, ts = tonumber(s[1]) or cap, tonumber(s[2]) or now
tokens = math.min(cap, tokens + (now - ts) * rate)
local ok = 0
if tokens >= 1 then tokens = tokens - 1; ok = 1 end
redis.call('HSET', key, 't', tokens, 'ts', now)
redis.call('EXPIRE', key, math.ceil(cap / rate) + 60)
return ok
"""
_local: dict[str, TokenBucket] = {}


@lru_cache
def _client(url: str) -> Any:
    import redis

    return redis.Redis.from_url(url, socket_connect_timeout=0.3, socket_timeout=0.3)


def allow(key: str, *, rate_per_sec: float, capacity: int, redis_url: str | None) -> bool:
    if redis_url and not key.startswith("test:"):
        try:
            return bool(_client(redis_url).eval(_LUA, 1, f"rl:{key}", rate_per_sec, capacity, time.time()))
        except Exception:  # any Valkey problem: degrade to the local bucket, never to "no limit"
            log.warning("rate_limiter_fallback")
    bucket = _local.setdefault(key, TokenBucket(rate_per_sec=rate_per_sec, capacity=capacity))
    return bucket.try_acquire()


def reset_local() -> None:  # tests
    _local.clear()
