"""
Reels API Routes — Submit and monitor reel engagement jobs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, HttpUrl
from sqlalchemy import func, select

from instaflow.config.logging import get_logger
from instaflow.storage.database import get_db_session
from instaflow.storage.models import Reel

logger = get_logger(__name__)
router = APIRouter()


# ── Request / Response Models ──────────────────────────────────────────

class ReelSubmitRequest(BaseModel):
    urls: list[str]
    account_id: int = 1


class ReelResponse(BaseModel):
    id: int
    url: str
    creator_username: Optional[str] = None
    follow_status: Optional[str] = None
    comment_text: Optional[str] = None
    job_status: str
    submitted_at: Optional[str] = None

    class Config:
        from_attributes = True


class ReelSubmitResponse(BaseModel):
    submitted: int
    duplicates: int
    task_ids: list[str]


# ── Endpoints ──────────────────────────────────────────────────────────

@router.post("/submit", response_model=ReelSubmitResponse)
async def submit_reels(request: ReelSubmitRequest):
    """
    Submit one or more reel URLs for engagement processing.
    Duplicate URLs are silently skipped.
    """
    from instaflow.workers.task_reel import process_reel

    submitted = 0
    duplicates = 0
    task_ids: list[str] = []

    for url in request.urls:
        url = url.strip()
        if not url:
            continue

        async with get_db_session() as db:
            # Check for duplicates
            existing = await db.execute(
                select(Reel).where(Reel.url == url)
            )
            if existing.scalar_one_or_none():
                duplicates += 1
                continue

            # Insert new reel
            reel = Reel(
                url=url,
                job_status="pending",
                submitted_at=datetime.now(tz=timezone.utc),
            )
            db.add(reel)

        # Enqueue Celery task
        result = process_reel.apply_async(
            kwargs={"reel_url": url, "account_id": request.account_id}
        )
        task_ids.append(result.id)
        submitted += 1

        logger.info("api.reel.submitted", url=url, task_id=result.id)

    return ReelSubmitResponse(
        submitted=submitted,
        duplicates=duplicates,
        task_ids=task_ids,
    )


@router.get("", response_model=list[ReelResponse])
async def list_reels(
    status: Optional[str] = Query(None, description="Filter by job_status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List all reels with optional status filter and pagination."""
    async with get_db_session() as db:
        stmt = select(Reel).order_by(Reel.submitted_at.desc())

        if status:
            stmt = stmt.where(Reel.job_status == status)

        stmt = stmt.limit(limit).offset(offset)
        result = await db.execute(stmt)
        reels = result.scalars().all()

    return [
        ReelResponse(
            id=r.id,
            url=r.url,
            creator_username=r.creator_username,
            follow_status=r.follow_status,
            comment_text=r.comment_text,
            job_status=r.job_status,
            submitted_at=r.submitted_at.isoformat() if r.submitted_at else None,
        )
        for r in reels
    ]


@router.get("/{reel_id}", response_model=ReelResponse)
async def get_reel(reel_id: int):
    """Get detailed information about a single reel job."""
    async with get_db_session() as db:
        result = await db.execute(select(Reel).where(Reel.id == reel_id))
        reel = result.scalar_one_or_none()

    if not reel:
        raise HTTPException(status_code=404, detail="Reel not found")

    return ReelResponse(
        id=reel.id,
        url=reel.url,
        creator_username=reel.creator_username,
        follow_status=reel.follow_status,
        comment_text=reel.comment_text,
        job_status=reel.job_status,
        submitted_at=reel.submitted_at.isoformat() if reel.submitted_at else None,
    )
