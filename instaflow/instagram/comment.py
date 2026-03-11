"""
Comment Posting — Module 3 (Instagram Interface)

Posts the trigger keyword "link" on reel media.
The comment text is loaded from comments.yaml via the comment engine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from instaflow.config.logging import get_logger
from instaflow.stealth.comment_engine import get_comment_text

logger = get_logger(__name__)


class CommentPoster:
    """
    Posts comments on Instagram reels.

    The only comment this tool ever posts is "link" (configurable
    via comments.yaml but that's the expected value).
    """

    def __init__(self, ig_client: Any) -> None:
        self._client = ig_client

    async def post_comment(self, media_id: str) -> dict[str, Any]:
        """
        Post the trigger comment ("link") on a reel.

        Parameters
        ----------
        media_id : Instagram media ID (resolved from reel URL).

        Returns
        -------
        dict with comment details and timestamp.
        """
        comment_text = get_comment_text()

        result = await self._client.safe_call(
            self._client.api.media_comment,
            media_id,
            comment_text,
        )

        logger.info(
            "comment.posted",
            media_id=media_id,
            text=comment_text,
            comment_id=getattr(result, "pk", None),
        )

        return {
            "comment_text": comment_text,
            "comment_id": str(getattr(result, "pk", "")),
            "media_id": media_id,
            "posted_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    async def like_media(self, media_id: str) -> bool:
        """
        Optionally like a reel (called ~30% of the time for human signals).
        """
        try:
            result = await self._client.safe_call(
                self._client.api.media_like, media_id
            )
            logger.info("comment.liked", media_id=media_id)
            return bool(result)
        except Exception:
            logger.warning("comment.like_failed", media_id=media_id)
            return False
