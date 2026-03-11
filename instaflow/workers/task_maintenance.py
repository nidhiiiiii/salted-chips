"""
Maintenance Tasks — Module 4.2

Periodic tasks run by Celery Beat:
  • health_check:       Ping IG API, update health score (every 30 min)
  • export_excel:       Export new extracted links to Excel (every hour)
  • recover_proxies:    Move cooled proxies back to active (every 45 min)
  • check_follow_backs: Check if followed creators followed back (every 6h)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from instaflow.config.logging import get_logger
from instaflow.workers.celery_app import app

logger = get_logger(__name__)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Health Check ───────────────────────────────────────────────────────

@app.task(
    name="instaflow.workers.task_maintenance.health_check",
    queue="low",
)
def health_check(account_id: int) -> dict:
    """Ping Instagram API with a lightweight call to verify session health."""
    return _run_async(_health_check_async(account_id))


async def _health_check_async(account_id: int) -> dict:
    from instaflow.core.health_monitor import HealthMonitor
    from instaflow.instagram.client import InstagramClient
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Account

    from sqlalchemy import select, update

    async with get_db_session() as db:
        result = await db.execute(
            select(Account).where(Account.id == account_id)
        )
        account = result.scalar_one_or_none()
        if not account:
            return {"status": "account_not_found"}

    try:
        ig_client = InstagramClient(
            account_id=account_id,
            ig_username=account.ig_username,
        )
        await ig_client.initialize()

        # Lightweight call: fetch own profile
        await ig_client.safe_call(
            ig_client.api.account_info
        )

        # Positive signal
        new_score = HealthMonitor.apply_signal(account.health_score, "clean_session")
        mode = HealthMonitor.get_mode(new_score)

        async with get_db_session() as db:
            await db.execute(
                update(Account)
                .where(Account.id == account_id)
                .values(health_score=new_score)
            )

        await ig_client.close()

        logger.info(
            "maintenance.health_check.ok",
            account_id=account_id,
            score=new_score,
            mode=mode.value,
        )
        return {"status": "healthy", "score": new_score, "mode": mode.value}

    except Exception as exc:
        # Determine which negative signal
        signal = "login_failure"
        error_str = str(exc).lower()
        if "challenge" in error_str:
            signal = "checkpoint_challenge"
        elif "rate" in error_str or "429" in error_str:
            signal = "rate_limit_429"
        elif "captcha" in error_str:
            signal = "captcha_triggered"

        new_score = HealthMonitor.apply_signal(account.health_score, signal)

        async with get_db_session() as db:
            status = "quarantine" if HealthMonitor.should_quarantine(new_score) else account.status
            await db.execute(
                update(Account)
                .where(Account.id == account_id)
                .values(health_score=new_score, status=status)
            )

        logger.error(
            "maintenance.health_check.failed",
            account_id=account_id,
            signal=signal,
            score=new_score,
            error=str(exc),
        )
        return {"status": "unhealthy", "score": new_score, "signal": signal}


# ── Excel Export ───────────────────────────────────────────────────────

@app.task(
    name="instaflow.workers.task_maintenance.export_excel",
    queue="low",
)
def export_excel() -> dict:
    """Export all new (unexported) extracted links to Excel."""
    return _run_async(_export_excel_async())


async def _export_excel_async() -> dict:
    from instaflow.storage.database import get_db_session
    from instaflow.storage.excel_exporter import export_links
    from instaflow.storage.models import DmMessage, ExtractedLink, Reel

    from sqlalchemy import select, update
    from sqlalchemy.orm import joinedload

    async with get_db_session() as db:
        result = await db.execute(
            select(ExtractedLink)
            .where(ExtractedLink.exported_to_excel == False)  # noqa: E712
            .options(
                joinedload(ExtractedLink.dm_message).joinedload(DmMessage.reel)
            )
            .order_by(ExtractedLink.extracted_at)
        )
        links = result.scalars().unique().all()

    if not links:
        return {"status": "no_new_links"}

    records = []
    link_ids = []
    for link in links:
        reel = link.dm_message.reel if link.dm_message else None
        records.append(
            {
                "reel_url": reel.url if reel else "",
                "creator_username": reel.creator_username if reel else "",
                "dm_message_text": link.dm_message.message_text if link.dm_message else "",
                "original_url": link.original_url or "",
                "final_url": link.final_url or "",
                "redirect_chain": link.redirect_chain or [],
                "extraction_method": link.extraction_method or "",
                "extracted_at": link.extracted_at.isoformat() if link.extracted_at else "",
            }
        )
        link_ids.append(link.id)

    # Export
    paths = export_links(records)

    # Mark as exported
    async with get_db_session() as db:
        await db.execute(
            update(ExtractedLink)
            .where(ExtractedLink.id.in_(link_ids))
            .values(exported_to_excel=True)
        )

    logger.info(
        "maintenance.export_excel.done",
        exported_count=len(records),
        paths=paths,
    )
    return {"status": "exported", "count": len(records), "files": paths}


# ── Proxy Recovery ─────────────────────────────────────────────────────

@app.task(
    name="instaflow.workers.task_maintenance.recover_proxies",
    queue="low",
)
def recover_proxies() -> dict:
    """Move rested proxies from cooling → active."""
    return _run_async(_recover_proxies_async())


async def _recover_proxies_async() -> dict:
    from instaflow.core.proxy_manager import ProxyManager
    from instaflow.storage.database import get_db_session

    async with get_db_session() as db:
        manager = ProxyManager(db)
        count = await manager.recover_cooling(cool_down_minutes=30)

    return {"status": "recovered", "count": count}


# ── Follow-Back Checker ───────────────────────────────────────────────

@app.task(
    name="instaflow.workers.task_maintenance.check_follow_backs",
    queue="low",
)
def check_follow_backs(account_id: int) -> dict:
    """Check if followed creators have followed back."""
    return _run_async(_check_follow_backs_async(account_id))


async def _check_follow_backs_async(account_id: int) -> dict:
    from instaflow.instagram.client import InstagramClient
    from instaflow.instagram.follow import FollowManager
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Account, Follow
    from instaflow.storage.redis_client import get_redis

    from sqlalchemy import select, update

    redis = await get_redis()

    async with get_db_session() as db:
        result = await db.execute(
            select(Account).where(Account.id == account_id)
        )
        account = result.scalar_one_or_none()
        if not account:
            return {"status": "account_not_found"}

    ig_client = InstagramClient(
        account_id=account_id,
        ig_username=account.ig_username,
    )
    await ig_client.initialize()

    follow_mgr = FollowManager(ig_client, redis, account_id)

    # Get unchecked follows
    async with get_db_session() as db:
        result = await db.execute(
            select(Follow).where(
                Follow.account_id == account_id,
                Follow.follow_back == False,  # noqa: E712
            )
        )
        follows = result.scalars().all()

    checked = 0
    follow_backs = 0

    for follow in follows:
        try:
            followed_back = await follow_mgr.check_follow_back(follow.creator_user_id)
            if followed_back:
                async with get_db_session() as db:
                    await db.execute(
                        update(Follow)
                        .where(Follow.id == follow.id)
                        .values(
                            follow_back=True,
                            follow_back_at=datetime.now(tz=timezone.utc),
                        )
                    )
                follow_backs += 1
            checked += 1
        except Exception:
            logger.warning(
                "maintenance.follow_back_check_failed",
                creator_user_id=follow.creator_user_id,
            )

    await ig_client.close()

    logger.info(
        "maintenance.follow_backs_checked",
        checked=checked,
        follow_backs=follow_backs,
    )
    return {"status": "checked", "total": checked, "follow_backs": follow_backs}
