"""
DM Watch Task — Module 4.2

Polls DM thread with a specific creator, scanning for CTA messages.
If a CTA is detected → emits extract_link task.
If deadline exceeded (24h) → marks NO_DM_RECEIVED, stops polling.
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


@app.task(
    bind=True,
    name="instaflow.workers.task_dm.watch_dm",
    max_retries=5,
    default_retry_delay=300,
    acks_late=True,
)
def watch_dm(self, creator_user_id: int, account_id: int, deadline_iso: str) -> dict:
    """
    Poll DM thread for CTA messages from a specific creator.

    Self-re-enqueues after each poll cycle until either:
      (a) A CTA is detected → triggers extract_link
      (b) Deadline passes → logs NO_DM_RECEIVED
    """
    return _run_async(
        _watch_dm_async(self, creator_user_id, account_id, deadline_iso)
    )


async def _watch_dm_async(
    task, creator_user_id: int, account_id: int, deadline_iso: str,
) -> dict:
    from instaflow.instagram.client import InstagramClient
    from instaflow.instagram.dm_monitor import DmMonitor
    from instaflow.stealth.rate_limiter import RateLimiter
    from instaflow.stealth.timing import Delay
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Account, DmMessage, Reel, TaskLog
    from instaflow.storage.redis_client import get_redis

    from sqlalchemy import select

    redis = await get_redis()
    deadline = datetime.fromisoformat(deadline_iso)
    task_id = task.request.id or "unknown"
    start_time = datetime.now(tz=timezone.utc)

    # Check if deadline has passed
    if datetime.now(tz=timezone.utc) >= deadline:
        logger.info(
            "task.dm.deadline_expired",
            creator_user_id=creator_user_id,
            account_id=account_id,
        )
        # Remove from watch list
        dm_mon_cleanup = DmMonitor(None, redis, account_id)
        await dm_mon_cleanup.remove_watched_creator(creator_user_id)

        # Log task completion
        async with get_db_session() as db:
            task_log = TaskLog(
                task_id=task_id,
                task_type="watch_dm",
                account_id=account_id,
                status="no_dm_received",
                started_at=start_time,
                completed_at=datetime.now(tz=timezone.utc),
            )
            db.add(task_log)

        return {"status": "no_dm_received", "creator_user_id": creator_user_id}

    # Initialize client
    async with get_db_session() as db:
        result = await db.execute(
            select(Account).where(Account.id == account_id)
        )
        account = result.scalar_one_or_none()
        if not account or account.status == "quarantine":
            return {"status": "skipped", "reason": "account_unavailable"}

    ig_client = InstagramClient(
        account_id=account_id,
        ig_username=account.ig_username,
    )
    await ig_client.initialize()

    rate_limiter = RateLimiter(redis, account_id, account.health_score)
    await rate_limiter.wait_until_clear("dm_reads")

    dm_monitor = DmMonitor(ig_client, redis, account_id)

    try:
        # Poll for CTAs
        cta_results = await dm_monitor.poll_creator_dm(creator_user_id)
        await rate_limiter.record("dm_reads")

        if cta_results:
            # CTA detected! Process each detection
            for cta in cta_results:
                # Save DM to database
                async with get_db_session() as db:
                    # Find the reel associated with this creator
                    reel_result = await db.execute(
                        select(Reel)
                        .where(Reel.creator_user_id == creator_user_id)
                        .order_by(Reel.submitted_at.desc())
                        .limit(1)
                    )
                    reel = reel_result.scalar_one_or_none()
                    reel_id = reel.id if reel else None

                    dm_msg = DmMessage(
                        reel_id=reel_id,
                        creator_user_id=creator_user_id,
                        message_id=cta["message_id"],
                        message_text=cta["message_text"],
                        cta_detected=True,
                        cta_confidence=cta["confidence"],
                        received_at=datetime.fromisoformat(cta["received_at"]),
                    )
                    db.add(dm_msg)
                    await db.flush()
                    dm_msg_id = dm_msg.id

                # Trigger link extraction for each URL found
                for url in cta["urls"]:
                    from instaflow.workers.task_extract import extract_link

                    extract_link.apply_async(
                        kwargs={
                            "dm_message_id": dm_msg_id,
                            "raw_url": url,
                            "account_id": account_id,
                            "creator_user_id": creator_user_id,
                        },
                    )

                logger.info(
                    "task.dm.cta_detected",
                    creator_user_id=creator_user_id,
                    urls=cta["urls"],
                    confidence=cta["confidence"],
                )

            # Done watching this creator
            await dm_monitor.remove_watched_creator(creator_user_id)
            await ig_client.close()

            # Log task completion
            async with get_db_session() as db:
                task_log = TaskLog(
                    task_id=task_id,
                    task_type="watch_dm",
                    account_id=account_id,
                    status="cta_found",
                    started_at=start_time,
                    completed_at=datetime.now(tz=timezone.utc),
                )
                db.add(task_log)

            return {"status": "cta_found", "results": cta_results}

        else:
            # No CTA yet — re-enqueue with DM poll delay
            await ig_client.close()

            # Re-schedule self
            watch_dm.apply_async(
                kwargs={
                    "creator_user_id": creator_user_id,
                    "account_id": account_id,
                    "deadline_iso": deadline_iso,
                },
                countdown=int(5 * 60 + (3 * 60 * __import__("random").random())),
            )

            logger.debug(
                "task.dm.no_cta_yet",
                creator_user_id=creator_user_id,
            )
            return {"status": "polling", "creator_user_id": creator_user_id}

    except Exception as exc:
        logger.exception("task.dm.error", creator_user_id=creator_user_id)
        await ig_client.close()

        # Log task failure
        try:
            async with get_db_session() as db:
                task_log = TaskLog(
                    task_id=task_id,
                    task_type="watch_dm",
                    account_id=account_id,
                    status="failed",
                    error_message=str(exc),
                    retries=task.request.retries,
                    started_at=start_time,
                    completed_at=datetime.now(tz=timezone.utc),
                )
                db.add(task_log)
        except Exception:
            pass

        raise task.retry(exc=exc)
