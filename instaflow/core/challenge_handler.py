"""
Challenge Resolution Handler — Module 1.4

Handles Instagram checkpoint challenges (SMS, email, CAPTCHA).

Flow:
  1. instagrapi raises a challenge → this handler fires
  2. Current task state is saved to Redis
  3. Operator is notified via Telegram
  4. Handler polls Redis for operator-submitted code (10-min timeout)
  5. Code submitted → resume; Timeout → quarantine account
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from instaflow.config.logging import get_logger
from instaflow.config.settings import get_settings

logger = get_logger(__name__)

# Redis key patterns
_CHALLENGE_CODE_KEY = "challenge:{account_id}:code"
_CHALLENGE_STATUS_KEY = "challenge:{account_id}:status"

CHALLENGE_POLL_INTERVAL_SECONDS = 15
CHALLENGE_TIMEOUT_SECONDS = 600  # 10 minutes


class ChallengeHandler:
    """
    Wired into instagrapi's challenge callback system.

    The handler is stateless — all state lives in Redis so workers
    can restart without losing challenge context.
    """

    def __init__(self, redis_client: Any, account_id: int, ig_username: str) -> None:
        self._redis = redis_client
        self._account_id = account_id
        self._ig_username = ig_username

    async def on_challenge(self, challenge_type: str) -> Optional[str]:
        """
        Called by instagrapi when Instagram issues a challenge.

        Returns the resolution code if the operator provides one,
        or None if the timeout expires.
        """
        logger.warning(
            "challenge.detected",
            account_id=self._account_id,
            username=self._ig_username,
            challenge_type=challenge_type,
        )

        # Mark challenge in-progress
        status_key = _CHALLENGE_STATUS_KEY.format(account_id=self._account_id)
        code_key = _CHALLENGE_CODE_KEY.format(account_id=self._account_id)

        await self._redis.set(
            status_key,
            f"waiting|{challenge_type}|{datetime.now(tz=timezone.utc).isoformat()}",
            ex=CHALLENGE_TIMEOUT_SECONDS + 60,
        )

        # Notify operator via Telegram
        await self._send_telegram_alert(challenge_type)

        # Poll for operator-submitted code
        elapsed = 0
        while elapsed < CHALLENGE_TIMEOUT_SECONDS:
            code = await self._redis.get(code_key)
            if code:
                await self._redis.delete(code_key)
                await self._redis.set(status_key, "resolved", ex=3600)
                logger.info(
                    "challenge.resolved",
                    account_id=self._account_id,
                    elapsed_seconds=elapsed,
                )
                return code.decode() if isinstance(code, bytes) else code

            await asyncio.sleep(CHALLENGE_POLL_INTERVAL_SECONDS)
            elapsed += CHALLENGE_POLL_INTERVAL_SECONDS

        # Timeout — quarantine
        await self._redis.set(status_key, "timeout", ex=86400)
        logger.error(
            "challenge.timeout",
            account_id=self._account_id,
            timeout_seconds=CHALLENGE_TIMEOUT_SECONDS,
        )
        return None

    async def submit_code(self, code: str) -> None:
        """
        Called by the API when the operator submits a resolution code.
        The polling loop in `on_challenge` will pick it up.
        """
        code_key = _CHALLENGE_CODE_KEY.format(account_id=self._account_id)
        await self._redis.set(code_key, code, ex=CHALLENGE_TIMEOUT_SECONDS)
        logger.info(
            "challenge.code_submitted",
            account_id=self._account_id,
        )

    async def _send_telegram_alert(self, challenge_type: str) -> None:
        """Fire-and-forget Telegram notification to operator."""
        settings = get_settings()
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            logger.warning("challenge.telegram_not_configured")
            return

        try:
            import httpx

            message = (
                f"🚨 *Challenge Detected*\n\n"
                f"**Account:** `{self._ig_username}`\n"
                f"**Type:** `{challenge_type}`\n"
                f"**Time:** {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                f"Submit resolution code via API:\n"
                f"`POST /api/challenge/resolve`\n"
                f"Body: `{{\"account_id\": {self._account_id}, \"code\": \"...\"}}`"
            )

            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": settings.telegram_chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                    },
                )
            logger.info("challenge.telegram_sent", account_id=self._account_id)
        except Exception:
            logger.exception("challenge.telegram_failed", account_id=self._account_id)
