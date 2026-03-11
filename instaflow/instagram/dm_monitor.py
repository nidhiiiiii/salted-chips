"""
DM Monitor & CTA Extractor — Module 5

Watches for DMs from creators the bot has engaged with, detects
CTA messages using keyword + URL pattern matching, and scores them
for confidence.

Only monitors threads with creators from the `reels` table
(via the `watched_creators` Redis set) — never scans unrelated DMs.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from instaflow.config.logging import get_logger
from instaflow.config.settings import get_settings
from instaflow.storage.redis_client import Keys

logger = get_logger(__name__)

_cta_config: dict[str, Any] | None = None


def _load_cta_config() -> dict[str, Any]:
    """Load CTA keywords and scoring config from YAML."""
    global _cta_config
    if _cta_config is not None:
        return _cta_config

    settings = get_settings()
    config_path = Path(settings.cta_keywords_yaml)

    if not config_path.exists():
        logger.warning("dm_monitor.cta_config_missing")
        _cta_config = {
            "primary_keywords": ["link", "click", "tap here"],
            "secondary_keywords": ["here", "now", "free"],
            "url_pattern": r"https?://[^\s<>\"{}|\\^`\[\]]+",
            "scoring": {
                "primary_keyword_match": 0.4,
                "secondary_keyword_match": 0.15,
                "url_present": 0.45,
                "multiple_keywords_bonus": 0.1,
            },
            "confidence_threshold": 0.7,
        }
        return _cta_config

    with open(config_path) as f:
        _cta_config = yaml.safe_load(f)
    return _cta_config


class CTADetector:
    """
    Scores DM messages for CTA intent using keyword + URL pattern matching.

    Confidence formula:
      score  = primary_keyword × 0.4
             + url_present × 0.45
             + secondary_keyword × 0.15
             + multi_keyword_bonus × 0.1
    """

    def __init__(self) -> None:
        self._config = _load_cta_config()
        self._url_re = re.compile(self._config["url_pattern"])
        self._primary = [kw.lower() for kw in self._config["primary_keywords"]]
        self._secondary = [kw.lower() for kw in self._config["secondary_keywords"]]
        self._scoring = self._config["scoring"]
        self._threshold = self._config["confidence_threshold"]

    def score_message(self, text: str) -> dict[str, Any]:
        """
        Analyse message text and return CTA detection result.

        Returns:
            {
                "is_cta": bool,
                "confidence": float,
                "urls": list[str],
                "matched_keywords": list[str],
            }
        """
        lower = text.lower()
        score = 0.0
        matched_keywords: list[str] = []

        # Primary keywords
        primary_hits = [kw for kw in self._primary if kw in lower]
        if primary_hits:
            score += self._scoring["primary_keyword_match"]
            matched_keywords.extend(primary_hits)

        # Secondary keywords
        secondary_hits = [kw for kw in self._secondary if kw in lower]
        if secondary_hits:
            score += self._scoring["secondary_keyword_match"]
            matched_keywords.extend(secondary_hits)

        # URL presence
        urls = self._url_re.findall(text)
        if urls:
            score += self._scoring["url_present"]

        # Multiple keywords bonus
        total_hits = len(primary_hits) + len(secondary_hits)
        if total_hits > 1:
            score += self._scoring["multiple_keywords_bonus"]

        # Clamp to [0, 1]
        score = min(score, 1.0)

        return {
            "is_cta": score >= self._threshold,
            "confidence": round(score, 3),
            "urls": urls,
            "matched_keywords": matched_keywords,
        }


class DmMonitor:
    """
    Polls DM threads with specific creators and detects CTA messages.

    Scope control: Only monitors threads whose creator_user_id is in
    the `watched_creators:{account_id}` Redis set.
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
        self._detector = CTADetector()

    async def add_watched_creator(self, creator_user_id: int) -> None:
        """Add a creator to the watch list after commenting on their reel."""
        key = Keys.watched_creators(self._account_id)
        await self._redis.sadd(key, str(creator_user_id))
        logger.info(
            "dm_monitor.watching",
            creator_user_id=creator_user_id,
            account_id=self._account_id,
        )

    async def is_watched(self, creator_user_id: int) -> bool:
        """Check if a creator is being monitored."""
        key = Keys.watched_creators(self._account_id)
        return await self._redis.sismember(key, str(creator_user_id))

    async def poll_creator_dm(self, creator_user_id: int) -> list[dict[str, Any]]:
        """
        Fetch new DMs from a specific creator and scan for CTAs.

        Returns a list of CTA detections (may be empty).
        """
        seen_key = Keys.seen_messages(self._account_id)

        # Get DM threads
        threads = await self._client.safe_call(
            self._client.api.direct_threads, amount=20
        )

        cta_results: list[dict[str, Any]] = []

        for thread in threads:
            # Find the thread with this creator
            thread_users = [u.pk for u in thread.users]
            if creator_user_id not in thread_users:
                continue

            # Fetch messages in this thread
            messages = await self._client.safe_call(
                self._client.api.direct_messages, thread.id, amount=10
            )

            for msg in messages:
                msg_id = str(msg.id)

                # Skip already-seen messages
                if await self._redis.sismember(seen_key, msg_id):
                    continue

                # Mark as seen
                await self._redis.sadd(seen_key, msg_id)

                # Only process messages FROM the creator (not our own)
                if msg.user_id != creator_user_id:
                    continue

                text = msg.text or ""
                if not text:
                    continue

                # Score for CTA
                detection = self._detector.score_message(text)

                logger.info(
                    "dm_monitor.message_scanned",
                    creator_user_id=creator_user_id,
                    message_id=msg_id,
                    confidence=detection["confidence"],
                    is_cta=detection["is_cta"],
                )

                if detection["is_cta"]:
                    cta_results.append(
                        {
                            "message_id": msg_id,
                            "thread_id": str(thread.id),
                            "creator_user_id": creator_user_id,
                            "message_text": text,
                            "urls": detection["urls"],
                            "confidence": detection["confidence"],
                            "received_at": (
                                msg.timestamp.isoformat()
                                if msg.timestamp
                                else datetime.now(tz=timezone.utc).isoformat()
                            ),
                        }
                    )

            break  # Only process the matching thread

        return cta_results

    async def remove_watched_creator(self, creator_user_id: int) -> None:
        """Stop monitoring a creator (e.g., after successful extraction or timeout)."""
        key = Keys.watched_creators(self._account_id)
        await self._redis.srem(key, str(creator_user_id))
        logger.info("dm_monitor.unwatched", creator_user_id=creator_user_id)
