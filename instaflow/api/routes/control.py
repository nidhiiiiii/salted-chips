"""
Control API Routes — Pause/resume workers, submit challenge codes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from instaflow.config.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


class ChallengeResolveRequest(BaseModel):
    account_id: int
    code: str


class ControlResponse(BaseModel):
    status: str
    message: str


@router.post("/pause", response_model=ControlResponse)
async def pause_workers():
    """Broadcast a pause signal to all Celery workers."""
    from instaflow.workers.celery_app import app as celery_app

    celery_app.control.broadcast("rate_limit", arguments={"task_name": "*", "rate_limit": "0/s"})
    logger.info("control.workers_paused")
    return ControlResponse(status="ok", message="Pause signal sent to all workers.")


@router.post("/resume", response_model=ControlResponse)
async def resume_workers():
    """Lift the rate limit, resuming all Celery workers."""
    from instaflow.workers.celery_app import app as celery_app

    celery_app.control.broadcast("rate_limit", arguments={"task_name": "*", "rate_limit": None})
    logger.info("control.workers_resumed")
    return ControlResponse(status="ok", message="Resume signal sent to all workers.")


@router.post("/challenge/resolve", response_model=ControlResponse)
async def resolve_challenge(request: ChallengeResolveRequest):
    """
    Submit a challenge resolution code for a specific account.

    The challenge handler polls Redis for this code.
    """
    from instaflow.storage.redis_client import Keys, get_redis

    redis = await get_redis()
    code_key = Keys.challenge_code(request.account_id)

    await redis.set(code_key, request.code, ex=600)  # 10-minute TTL
    logger.info("control.challenge_code_submitted", account_id=request.account_id)

    return ControlResponse(
        status="ok",
        message=f"Challenge code submitted for account {request.account_id}.",
    )
