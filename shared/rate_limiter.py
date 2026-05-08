"""
Rate Limiter for API calls.

Two backends:
  - ``MemoryRateLimiter``  — in-process, single-replica only. Default in dev.
  - ``RedisRateLimiter``   — sliding-window via a Redis sorted set; correct under
                             horizontal scaling.

Pick the backend with ``RATE_LIMITER_BACKEND=redis|memory``. Both implement the
same ``check_limit``/``wait_if_needed`` protocol so callers don't have to
change. When ``RATE_LIMITER_BACKEND=redis`` but the connection is unavailable
the limiter degrades to memory (best-effort) and logs a warning rather than
failing requests outright — fail-open is the right choice for a rate limiter
in front of revenue-critical paths, but the warning surfaces the misconfig.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Protocol

logger = logging.getLogger(__name__)


class RateLimitBackend(Protocol):
    async def check_limit(self, key: str, max_requests: int, window_seconds: int) -> bool: ...


class MemoryRateLimiter:
    """In-process sliding window. Not safe across replicas."""

    def __init__(self) -> None:
        self._limits: Dict[str, Dict] = {}
        self._lock = asyncio.Lock()

    async def check_limit(self, key: str, max_requests: int, window_seconds: int) -> bool:
        async with self._lock:
            now = datetime.utcnow()
            cutoff = now - timedelta(seconds=window_seconds)
            bucket = self._limits.setdefault(key, {"requests": []})
            bucket["requests"] = [t for t in bucket["requests"] if t > cutoff]
            if len(bucket["requests"]) >= max_requests:
                oldest = min(bucket["requests"])
                wait = (oldest + timedelta(seconds=window_seconds) - now).total_seconds()
                logger.warning(
                    "rate_limit_hit key=%s used=%d/%d wait=%.1fs",
                    key, len(bucket["requests"]), max_requests, wait,
                )
                return False
            bucket["requests"].append(now)
            return True


class RedisRateLimiter:
    """Sliding-window limiter using Redis sorted sets.

    Per key, we keep the timestamps of recent requests in a ZSET and trim
    entries older than the window. Replica-safe and atomic per key.
    """

    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._client = None
        self._fallback = MemoryRateLimiter()

    async def _get(self):
        if self._client is None:
            try:
                import redis.asyncio as aioredis  # type: ignore
                self._client = aioredis.from_url(
                    self._url, encoding="utf-8", decode_responses=True
                )
                # ping once to surface configuration errors early
                await self._client.ping()
            except Exception as e:
                logger.warning("redis_rate_limiter_unavailable err=%s; falling back to memory", e)
                self._client = None
        return self._client

    async def check_limit(self, key: str, max_requests: int, window_seconds: int) -> bool:
        client = await self._get()
        if client is None:
            return await self._fallback.check_limit(key, max_requests, window_seconds)
        ns_key = f"rl:{key}"
        now = time.time()
        cutoff = now - window_seconds
        try:
            async with client.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(ns_key, 0, cutoff)
                pipe.zcard(ns_key)
                pipe.zadd(ns_key, {f"{now}:{os.getpid()}": now})
                pipe.expire(ns_key, window_seconds + 1)
                _, current, _, _ = await pipe.execute()
            if current >= max_requests:
                # Roll back the entry we just added so we don't count this
                # rejected request against the bucket.
                await client.zremrangebyscore(ns_key, now, now)
                logger.warning(
                    "rate_limit_hit key=%s used=%d/%d", key, current, max_requests,
                )
                return False
            return True
        except Exception as e:
            logger.warning("redis_rate_limiter_error err=%s; failing open", e)
            return True


def _build_limiter() -> RateLimitBackend:
    backend = os.getenv("RATE_LIMITER_BACKEND", "memory").strip().lower()
    if backend == "redis":
        url = os.getenv("REDIS_URL") or os.getenv("UPSTASH_REDIS_URL")
        if not url:
            logger.warning("RATE_LIMITER_BACKEND=redis but no REDIS_URL; falling back to memory")
            return MemoryRateLimiter()
        return RedisRateLimiter(url)
    return MemoryRateLimiter()


# Public surface — preserved from the original module so callers don't change.
class RateLimiter:
    def __init__(self) -> None:
        self._impl = _build_limiter()

    async def check_limit(
        self,
        key: str,
        max_requests: int,
        window_seconds: int,
    ) -> bool:
        return await self._impl.check_limit(key, max_requests, window_seconds)

    async def wait_if_needed(
        self,
        key: str,
        max_requests: int,
        window_seconds: int,
        max_wait: int = 60,
    ) -> bool:
        waited = 0
        while not await self.check_limit(key, max_requests, window_seconds):
            if waited >= max_wait:
                logger.error("max_wait_exceeded key=%s wait=%ds", key, max_wait)
                return False
            await asyncio.sleep(1)
            waited += 1
        return True


# Rate limit configurations for different APIs
RATE_LIMITS = {
    "google_search_console": {"max_requests": 1200, "window_seconds": 60},
    "google_ads_api": {"max_requests": 15000, "window_seconds": 60},
    "google_indexing_api": {"max_requests": 200, "window_seconds": 86400},
    "google_pagespeed": {"max_requests": 25000, "window_seconds": 86400},
    "anthropic_api": {"max_requests": 50, "window_seconds": 60},
    "g2_scraping": {"max_requests": 10, "window_seconds": 60},
    "capterra_scraping": {"max_requests": 10, "window_seconds": 60},
    "trustpilot_scraping": {"max_requests": 10, "window_seconds": 60},
    "twitter_api": {"max_requests": 300, "window_seconds": 900},
}


rate_limiter = RateLimiter()


async def rate_limit(api_name: str) -> bool:
    if api_name not in RATE_LIMITS:
        logger.warning(f"No rate limit configured for {api_name}")
        return True
    cfg = RATE_LIMITS[api_name]
    return await rate_limiter.check_limit(api_name, cfg["max_requests"], cfg["window_seconds"])
