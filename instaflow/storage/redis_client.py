"""
Redis Client — Module 6.2

Thin wrapper around redis.asyncio providing:
  • Connection pooling via a singleton client
  • Structured key namespace helpers
  • Graceful startup/shutdown hooks

All Redis key patterns are documented in the architecture spec.
"""

from __future__ import annotations

from typing import Any, Optional

import redis.asyncio as aioredis

from instaflow.config.logging import get_logger
from instaflow.config.settings import get_settings

logger = get_logger(__name__)

_pool: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    """Return the singleton async Redis client (creates on first call)."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = aioredis.from_url(
            settings.redis_url,
            decode_responses=False,  # We handle encoding ourselves
            max_connections=20,
        )
        # Quick connectivity check
        await _pool.ping()
        logger.info("redis.connected", url=settings.redis_url.split("@")[-1])
    return _pool


async def close_redis() -> None:
    """Close the Redis connection pool (call on app shutdown)."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("redis.closed")


# ── Key Namespace Helpers ──────────────────────────────────────────────
# These keep Redis key patterns in one place so typos don't creep in.

class Keys:
    """Structured Redis key builders."""

    @staticmethod
    def session_state(account_id: int) -> str:
        return f"session:{account_id}:state"

    @staticmethod
    def rate_limit(account_id: int, action: str) -> str:
        return f"ratelimit:{account_id}:{action}"

    @staticmethod
    def seen_messages(account_id: int) -> str:
        return f"seen_messages:{account_id}"

    @staticmethod
    def watched_creators(account_id: int) -> str:
        return f"watched_creators:{account_id}"

    @staticmethod
    def friendship_cache(account_id: int, user_id: int) -> str:
        return f"friendship_cache:{account_id}:{user_id}"

    @staticmethod
    def health(account_id: int) -> str:
        return f"health:{account_id}"

    @staticmethod
    def challenge_code(account_id: int) -> str:
        return f"challenge:{account_id}:code"

    @staticmethod
    def challenge_status(account_id: int) -> str:
        return f"challenge:{account_id}:status"

    @staticmethod
    def task_state(task_id: str) -> str:
        return f"task_state:{task_id}"
