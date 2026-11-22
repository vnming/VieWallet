"""Fixed-window rate limiting on Redis (ADR-0006: Redis is not a source of truth).

If Redis is unavailable the limiter fails open and logs a warning: availability of the
wallet is preferred over strict throttling, and the other controls (account lockout, PIN
lock) are stored in PostgreSQL and keep working.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.clock import Clock
from app.core.errors import AppError

logger = structlog.get_logger(__name__)


class RateLimitedError(AppError):
    code = "RATE_LIMITED"
    status = 429


@dataclass(frozen=True, slots=True)
class RateLimit:
    name: str
    limit: int
    window: timedelta


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    count: int
    retry_after_seconds: int


class RateLimiter:
    def __init__(self, redis: Redis, clock: Clock, *, prefix: str = "vw:rl") -> None:
        self._redis = redis
        self._clock = clock
        self._prefix = prefix

    def _key(self, rule: RateLimit, subject: str) -> tuple[str, int]:
        window = int(rule.window.total_seconds())
        now = int(self._clock.now().timestamp())
        bucket = now // window
        retry_after = (bucket + 1) * window - now
        return f"{self._prefix}:{rule.name}:{subject}:{bucket}", retry_after

    async def hit(self, rule: RateLimit, subject: str) -> RateLimitResult:
        key, retry_after = self._key(rule, subject)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, int(rule.window.total_seconds()) + 1)
                count, _ = await pipe.execute()
        except (RedisError, OSError) as exc:
            logger.warning("rate_limiter_unavailable", rule=rule.name, error=type(exc).__name__)
            return RateLimitResult(allowed=True, count=0, retry_after_seconds=0)
        return RateLimitResult(
            allowed=int(count) <= rule.limit, count=int(count), retry_after_seconds=retry_after
        )

    async def enforce(self, rule: RateLimit, subject: str) -> None:
        """Count one hit and raise ``RateLimitedError`` (HTTP 429) when over the limit."""
        result = await self.hit(rule, subject)
        if not result.allowed:
            raise RateLimitedError(headers={"Retry-After": str(max(result.retry_after_seconds, 1))})

    async def reset(self, rule: RateLimit, subject: str) -> None:
        key, _ = self._key(rule, subject)
        try:
            await self._redis.delete(key)
        except RedisError, OSError:
            logger.warning("rate_limiter_unavailable", rule=rule.name)
