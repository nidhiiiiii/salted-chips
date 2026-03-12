"""
Account Health Monitor — Module 1.3

Maintains a numeric health_score (0–100) per account in PostgreSQL.
Score drives operational mode:

  >= 70   → Normal operation
  40–69   → Conservative mode (halved rate limits)
  < 40    → Quarantine (all activity paused, operator alerted)

The monitor is called by workers after every Instagram API interaction
so the score stays current.
"""

from __future__ import annotations

from enum import Enum

from instaflow.config.logging import get_logger

logger = get_logger(__name__)


class OperationMode(str, Enum):
    NORMAL = "normal"
    CONSERVATIVE = "conservative"
    QUARANTINE = "quarantine"


# ── Signal weights ─────────────────────────────────────────────────────

NEGATIVE_SIGNALS: dict[str, int] = {
    "checkpoint_challenge": -20,
    "rate_limit_429": -10,
    "login_failure": -15,
    "captcha_triggered": -25,
    "action_blocked": -15,
    "consent_required": -10,
    "feedback_required": -10,
}

POSITIVE_SIGNALS: dict[str, int] = {
    "clean_session": 2,
    "successful_comment": 1,
    "successful_follow": 1,
    "successful_dm_read": 1,
}


class HealthMonitor:
    """
    Stateless score calculator.

    The actual score lives in PostgreSQL (accounts.health_score).
    This class provides the math + mode logic; callers are responsible
    for reading/writing the DB.
    """

    @staticmethod
    def apply_signal(current_score: int, signal_name: str) -> int:
        """
        Adjust *current_score* by the weight of *signal_name*.
        Returns the new clamped score (0–100).
        """
        delta = NEGATIVE_SIGNALS.get(signal_name, 0) or POSITIVE_SIGNALS.get(signal_name, 0)
        if delta == 0:
            logger.warning("health.unknown_signal", signal=signal_name)
            return current_score

        new_score = max(0, min(100, current_score + delta))

        logger.info(
            "health.signal_applied",
            signal=signal_name,
            delta=delta,
            old_score=current_score,
            new_score=new_score,
        )
        return new_score

    @staticmethod
    def get_mode(score: int) -> OperationMode:
        """Derive operational mode from health score."""
        if score >= 70:
            return OperationMode.NORMAL
        if score >= 40:
            return OperationMode.CONSERVATIVE
        return OperationMode.QUARANTINE

    @staticmethod
    def rate_limit_multiplier(score: int) -> float:
        """
        Returns a multiplier for rate-limit windows.

        Normal  → 1.0  (unchanged)
        Conservative → 0.5  (halved throughput)
        Quarantine → 0.0  (no actions allowed)
        """
        mode = HealthMonitor.get_mode(score)
        if mode is OperationMode.NORMAL:
            return 1.0
        if mode is OperationMode.CONSERVATIVE:
            return 0.5
        return 0.0

    @staticmethod
    def should_quarantine(score: int) -> bool:
        return score < 40

    @staticmethod
    async def notify_quarantine(ig_username: str, score: int) -> None:
        """Send Telegram alert when account is quarantined."""
        from instaflow.config.settings import get_settings

        settings = get_settings()
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            logger.warning("health.telegram_not_configured")
            return

        try:
            import httpx

            message = (
                f"🚨 *Account Quarantined*\n\n"
                f"**Account:** `{ig_username}`\n"
                f"**Health Score:** `{score}`\n"
                f"**Status:** All activity paused\n\n"
                f"Please review the account health and resolve any challenges "
                f"before resuming operations."
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
            logger.info("health.quarantine_alert_sent", username=ig_username, score=score)
        except Exception:
            logger.exception("health.quarantine_alert_failed", username=ig_username)
