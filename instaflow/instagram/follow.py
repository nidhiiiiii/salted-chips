"""
Follow Management — Module 3

Handles:
  • Checking follow status via instagrapi friendship API
  • Executing follows with rate-limit awareness
  • Private account handling
  • Friendship status caching in Redis (1h TTL)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from instaflow.config.logging import get_logger
from instaflow.storage.redis_client import Keys

logger = get_logger(__name__)

FRIENDSHIP_CACHE_TTL = 3600  # 1 hour


class FollowManager:
    """
    Follow status detection and follow execution.

    Uses instagrapi's `user_friendship()` as the primary method
    (faster and more reliable than browser-based button detection).
    """

    def __init__(
        self,
        ig_client: Any,  # InstagramClient
        redis_client: Any,
        account_id: int,
    ) -> None:
        self._client = ig_client
        self._redis = redis_client
        self._account_id = account_id

    async def get_friendship_status(self, user_id: int) -> dict[str, Any]:
        """
        Check friendship status with Redis caching.

        Returns dict with: following, followed_by, blocking, is_private
        """
        cache_key = Keys.friendship_cache(self._account_id, user_id)

        # Check cache first
        cached = await self._redis.get(cache_key)
        if cached:
            logger.debug("follow.cache_hit", user_id=user_id)
            return json.loads(cached)

        # API call
        friendship = await self._client.safe_call(
            self._client.api.user_friendship, user_id
        )

        status = {
            "following": friendship.following,
            "followed_by": friendship.followed_by,
            "blocking": friendship.blocking,
            "is_private": friendship.is_private,
            "incoming_request": friendship.incoming_request,
            "outgoing_request": friendship.outgoing_request,
        }

        # Cache for 1 hour
        await self._redis.set(
            cache_key,
            json.dumps(status),
            ex=FRIENDSHIP_CACHE_TTL,
        )

        logger.info(
            "follow.status_checked",
            user_id=user_id,
            following=status["following"],
            is_private=status["is_private"],
        )
        return status

    async def follow_user(self, user_id: int) -> dict[str, Any]:
        """
        Follow a user, handling private accounts correctly.

        Returns:
            {
                "action": "followed" | "already_following" | "follow_request_sent",
                "user_id": int,
                "timestamp": str,
            }
        """
        status = await self.get_friendship_status(user_id)

        if status["following"]:
            logger.info("follow.already_following", user_id=user_id)
            return {
                "action": "already_following",
                "user_id": user_id,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }

        # Execute follow
        result = await self._client.safe_call(
            self._client.api.user_follow, user_id
        )

        # Invalidate friendship cache
        cache_key = Keys.friendship_cache(self._account_id, user_id)
        await self._redis.delete(cache_key)

        action = "follow_request_sent" if status["is_private"] else "followed"
        logger.info(
            "follow.executed",
            user_id=user_id,
            action=action,
            api_result=result,
        )

        return {
            "action": action,
            "user_id": user_id,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }

    async def check_follow_back(self, user_id: int) -> bool:
        """Check if a previously followed creator has followed back."""
        status = await self.get_friendship_status(user_id)
        return status.get("followed_by", False)

    async def unfollow_user(self, user_id: int) -> bool:
        """Unfollow a user (for cleanup of non-DMing creators)."""
        result = await self._client.safe_call(
            self._client.api.user_unfollow, user_id
        )
        # Invalidate cache
        cache_key = Keys.friendship_cache(self._account_id, user_id)
        await self._redis.delete(cache_key)
        logger.info("follow.unfollowed", user_id=user_id, result=result)
        return result
