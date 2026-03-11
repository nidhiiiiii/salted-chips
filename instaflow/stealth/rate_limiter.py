"""
Redis Sliding-Window Rate Limiter — Module 2.3

Enforces per-action, per-account rate limits using Redis sorted sets.
Each action (follow, comment, dm_read) has independent counters so
hitting one limit doesn't block other action types.

Implementation: Sorted set where score = Unix timestamp.
To check a window: ZRANGEBYSCORE to count entries in [now - window, now].
To record: ZADD with current timestamp.

Conservative mode (health < 70) halves all limits automatically.
"""

from __future__ import annotations

import time
from typing import Any

from instaflow.config.logging import get_logger
from instaflow.config.settings import get_settings
from instaflow.core.health_monitor import HealthMonitor

logger = get_logger(__name__)

# Key pattern: ratelimit:{account_id}:{action_type}
_KEY_PATTERN = "ratelimit:{account_id}:{action}"


class RateLimitExceeded(Exception):
    """Raised when an action would violate rate limits."""

    def __init__(self, action: str, window: str, current: int, limit: int) -> None:
        self.action = action
        self.window = window
        self.current = current
        self.limit = limit
        super().__init__(
            f"Rate limit for '{action}' exceeded: {current}/{limit} in {window}"
        )


class RateLimiter:
    """
    Per-account, per-action sliding window rate limiter.

    Usage::

        limiter = RateLimiter(redis, account_id=1, health_score=85)
        if await limiter.can_proceed("follows"):
            await limiter.record("follows")
            # ... do the follow ...
    """

    # Default limits from settings
    _LIMITS: dict[str, dict[str, int]] = {}

    def __init__(
        self,
        redis_client: Any,
        account_id: int,
        health_score: int = 100,
    ) -> None:
        self._redis = redis_client
        self._account_id = account_id
        self._multiplier = HealthMonitor.rate_limit_multiplier(health_score)
        self._load_limits()

    def _load_limits(self) -> None:
        settings = get_settings()
        self._LIMITS = {
            "follows": {
                "per_hour": settings.rate_follows_per_hour,
                "per_day": settings.rate_follows_per_day,
            },
            "comments": {
                "per_hour": settings.rate_comments_per_hour,
                "per_day": settings.rate_comments_per_day,
            },
            "dm_reads": {
                "per_hour": settings.rate_dm_reads_per_hour,
                "per_day": 200,
            },
        }

    async def can_proceed(self, action: str) -> bool:
        """
        Check whether the action is within all rate-limit windows.
        Does NOT consume a slot — call `record()` after the action succeeds.
        """
        limits = self._LIMITS.get(action)
        if not limits:
            return True

        key = _KEY_PATTERN.format(account_id=self._account_id, action=action)
        now = time.time()

        # Clean old entries (> 24h)
        await self._redis.zremrangebyscore(key, 0, now - 86400)

        # Check hourly window
        hour_count = await self._redis.zcount(key, now - 3600, now)
        hour_limit = int(limits["per_hour"] * self._multiplier)
        if hour_count >= hour_limit:
            logger.info(
                "rate_limiter.blocked",
                action=action,
                window="hourly",
                count=hour_count,
                limit=hour_limit,
            )
            return False

        # Check daily window
        day_count = await self._redis.zcount(key, now - 86400, now)
        day_limit = int(limits["per_day"] * self._multiplier)
        if day_count >= day_limit:
            logger.info(
                "rate_limiter.blocked",
                action=action,
                window="daily",
                count=day_count,
                limit=day_limit,
            )
            return False

        return True

    async def record(self, action: str) -> None:
        """Record that an action was performed right now."""
        key = _KEY_PATTERN.format(account_id=self._account_id, action=action)
        now = time.time()
        # Use timestamp as both score and member (with microsecond uniqueness)
        await self._redis.zadd(key, {str(now): now})
        # Auto-expire the entire key after 25h to prevent leaks
        await self._redis.expire(key, 90000)

        logger.debug("rate_limiter.recorded", action=action, account_id=self._account_id)

    async def wait_until_clear(self, action: str) -> float:
        """
        Block until the rate limit window clears for the given action.
        Returns total seconds waited.  Used by workers to self-throttle.
        """
        import asyncio

        total_waited = 0.0
        poll_interval = 30.0  # seconds

        while not await self.can_proceed(action):
            logger.info(
                "rate_limiter.waiting",
                action=action,
                poll_interval=poll_interval,
            )
            await asyncio.sleep(poll_interval)
            total_waited += poll_interval

        return total_waited

    async def get_usage(self, action: str) -> dict[str, Any]:
        """Return current usage stats for an action (for dashboard/monitoring)."""
        limits = self._LIMITS.get(action, {})
        key = _KEY_PATTERN.format(account_id=self._account_id, action=action)
        now = time.time()

        hour_count = await self._redis.zcount(key, now - 3600, now)
        day_count = await self._redis.zcount(key, now - 86400, now)

        return {
            "action": action,
            "hourly": {
                "used": hour_count,
                "limit": int(limits.get("per_hour", 0) * self._multiplier),
            },
            "daily": {
                "used": day_count,
                "limit": int(limits.get("per_day", 0) * self._multiplier),
            },
            "multiplier": self._multiplier,
        }
