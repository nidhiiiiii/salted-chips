"""
Process Reel Task — Module 4.2 (Action Sequencer)

Entry point for all reel engagement jobs.  Follows the strict
action sequence from Module 2.5:

  1. Resolve reel URL → media_id → creator user_id
  2. Check follow status
  3. Delay (simulate viewing)
  4. Follow if needed
  5. Delay (simulate watching reel)
  6. Comment "link"
  7. Optionally like (~30%)
  8. Mark job COMPLETED
  9. Enqueue DM watch task (delayed 5–30 min)
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone

from instaflow.config.logging import get_logger
from instaflow.workers.celery_app import app

logger = get_logger(__name__)


def _run_async(coro):
    """Helper to run async code inside synchronous Celery tasks."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@app.task(
    bind=True,
    name="instaflow.workers.task_reel.process_reel",
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
    retry_backoff_max=900,
    acks_late=True,
)
def process_reel(self, reel_url: str, account_id: int) -> dict:
    """
    Main reel engagement task — follows the Action Sequencer.

    Retries: 3 attempts with exponential backoff (60s, 300s, 900s).
    On final failure: goes to dead-letter queue + Telegram alert.
    """
    return _run_async(_process_reel_async(self, reel_url, account_id))


async def _process_reel_async(task, reel_url: str, account_id: int) -> dict:
    """Async implementation of the reel processing pipeline."""
    from instaflow.instagram.client import InstagramClient
    from instaflow.instagram.comment import CommentPoster
    from instaflow.instagram.follow import FollowManager
    from instaflow.stealth.rate_limiter import RateLimiter
    from instaflow.stealth.timing import Delay
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Account, Follow, Reel, TaskLog
    from instaflow.storage.redis_client import get_redis

    from sqlalchemy import select, update

    redis = await get_redis()
    task_id = task.request.id or "unknown"
    start_time = datetime.now(tz=timezone.utc)

    logger.info("task.reel.start", reel_url=reel_url, account_id=account_id, task_id=task_id)

    try:
        # ── Step 0: Load account and initialize client ─────────────
        async with get_db_session() as db:
            result = await db.execute(
                select(Account).where(Account.id == account_id)
            )
            account = result.scalar_one_or_none()
            if not account:
                raise ValueError(f"Account {account_id} not found")

            if account.status == "quarantine":
                logger.warning("task.reel.quarantined", account_id=account_id)
                return {"status": "skipped", "reason": "account_quarantined"}

        ig_client = InstagramClient(
            account_id=account_id,
            ig_username=account.ig_username,
        )
        await ig_client.initialize()

        rate_limiter = RateLimiter(redis, account_id, account.health_score)
        follow_mgr = FollowManager(ig_client, redis, account_id)
        comment_poster = CommentPoster(ig_client)

        # ── Step 1: Resolve reel URL → media_id + creator ──────────
        media_pk = await ig_client.safe_call(
            ig_client.api.media_pk_from_url, reel_url
        )
        media_id = await ig_client.safe_call(
            ig_client.api.media_id, media_pk
        )
        media_info = await ig_client.safe_call(
            ig_client.api.media_info, media_pk
        )

        creator_user_id = media_info.user.pk
        creator_username = media_info.user.username

        logger.info(
            "task.reel.resolved",
            media_id=str(media_id),
            creator=creator_username,
            creator_id=creator_user_id,
        )

        # Save media info to DB
        async with get_db_session() as db:
            reel_result = await db.execute(
                select(Reel).where(Reel.url == reel_url)
            )
            reel = reel_result.scalar_one_or_none()
            reel_id = reel.id if reel else None

            await db.execute(
                update(Reel)
                .where(Reel.url == reel_url)
                .values(
                    media_id=str(media_id),
                    creator_username=creator_username,
                    creator_user_id=creator_user_id,
                    job_status="follow_pending",
                )
            )

        # ── Step 2: Check follow status ────────────────────────────
        friendship = await follow_mgr.get_friendship_status(creator_user_id)

        follow_result = {"action": "already_following"}

        if not friendship["following"]:
            # ── Step 3: Delay (simulate viewing) ───────────────────
            await Delay.before_follow()

            # Rate-limit check
            await rate_limiter.wait_until_clear("follows")

            # ── Step 4: Follow ─────────────────────────────────────
            follow_result = await follow_mgr.follow_user(creator_user_id)
            await rate_limiter.record("follows")

            # Record follow in database
            if follow_result["action"] in ("followed", "follow_request_sent"):
                async with get_db_session() as db:
                    follow_record = Follow(
                        account_id=account_id,
                        creator_user_id=creator_user_id,
                        creator_username=creator_username,
                        followed_at=datetime.now(tz=timezone.utc),
                        follow_back=False,
                    )
                    db.add(follow_record)

            # Handle private accounts
            if follow_result["action"] == "follow_request_sent":
                async with get_db_session() as db:
                    await db.execute(
                        update(Reel)
                        .where(Reel.url == reel_url)
                        .values(
                            follow_status="private_pending",
                            followed_at=datetime.now(tz=timezone.utc),
                            job_status="completed",
                        )
                    )
                logger.info("task.reel.private_account", creator=creator_username)
                # Still queue DM watch in case they respond
                _enqueue_dm_watch(creator_user_id, account_id)
                return {
                    "status": "completed_private",
                    "creator": creator_username,
                    "follow_action": "follow_request_sent",
                }

        # Update follow status in DB
        async with get_db_session() as db:
            await db.execute(
                update(Reel)
                .where(Reel.url == reel_url)
                .values(
                    follow_status=follow_result["action"],
                    followed_at=datetime.now(tz=timezone.utc),
                    job_status="commenting",
                )
            )

        # ── Step 5: Delay (simulate watching reel) ─────────────────
        await Delay.before_comment()

        # Rate limit check for comments
        await rate_limiter.wait_until_clear("comments")

        # ── Step 6: Comment "link" ─────────────────────────────────
        comment_result = await comment_poster.post_comment(str(media_pk))
        await rate_limiter.record("comments")

        # ── Step 7: Optional like (~30% chance) ────────────────────
        liked = False
        if random.random() < 0.3:
            await Delay.before_like()
            liked = await comment_poster.like_media(str(media_pk))

        # ── Step 8: Mark COMPLETED ─────────────────────────────────
        async with get_db_session() as db:
            await db.execute(
                update(Reel)
                .where(Reel.url == reel_url)
                .values(
                    comment_text=comment_result["comment_text"],
                    comment_posted_at=datetime.now(tz=timezone.utc),
                    job_status="completed",
                )
            )

        # ── Step 9: Enqueue DM watch (delayed 5–30 min) ───────────
        _enqueue_dm_watch(creator_user_id, account_id)

        # Add to watched creators in Redis
        from instaflow.instagram.dm_monitor import DmMonitor
        dm_mon = DmMonitor(ig_client, redis, account_id)
        await dm_mon.add_watched_creator(creator_user_id)

        await ig_client.close()

        result = {
            "status": "completed",
            "reel_url": reel_url,
            "creator": creator_username,
            "follow_action": follow_result["action"],
            "comment": comment_result["comment_text"],
            "liked": liked,
        }

        logger.info("task.reel.completed", **result)

        # Log task completion
        async with get_db_session() as db:
            task_log = TaskLog(
                task_id=task_id,
                task_type="process_reel",
                account_id=account_id,
                reel_id=reel_id,
                status="completed",
                started_at=start_time,
                completed_at=datetime.now(tz=timezone.utc),
            )
            db.add(task_log)

        return result

    except Exception as exc:
        logger.exception(
            "task.reel.failed",
            reel_url=reel_url,
            account_id=account_id,
            retry=task.request.retries,
        )

        # Update job status in DB
        try:
            async with get_db_session() as db:
                reel_result = await db.execute(
                    select(Reel).where(Reel.url == reel_url)
                )
                reel = reel_result.scalar_one_or_none()
                reel_id = reel.id if reel else None

                await db.execute(
                    update(Reel)
                    .where(Reel.url == reel_url)
                    .values(job_status="failed")
                )

                # Log task failure
                task_log = TaskLog(
                    task_id=task_id,
                    task_type="process_reel",
                    account_id=account_id,
                    reel_id=reel_id,
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


def _enqueue_dm_watch(creator_user_id: int, account_id: int) -> None:
    """Schedule DM monitoring with a random delay of 5–30 minutes."""
    from instaflow.workers.task_dm import watch_dm

    delay_seconds = random.randint(300, 1800)
    deadline = datetime.now(tz=timezone.utc) + timedelta(hours=24)

    watch_dm.apply_async(
        kwargs={
            "creator_user_id": creator_user_id,
            "account_id": account_id,
            "deadline_iso": deadline.isoformat(),
        },
        countdown=delay_seconds,
    )
    logger.info(
        "task.reel.dm_watch_enqueued",
        creator_user_id=creator_user_id,
        delay_minutes=round(delay_seconds / 60, 1),
    )
