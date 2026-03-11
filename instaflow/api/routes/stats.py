"""
Stats API Routes — Dashboard summary stats.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from instaflow.config.logging import get_logger
from instaflow.storage.database import get_db_session
from instaflow.storage.models import Account, ExtractedLink, Follow, Reel

logger = get_logger(__name__)
router = APIRouter()


class SummaryStats(BaseModel):
    total_reels_submitted: int
    reels_completed: int
    reels_failed: int
    reels_pending: int
    total_follows: int
    total_comments: int
    total_extracted_links: int
    accounts_active: int
    accounts_quarantined: int


@router.get("/summary", response_model=SummaryStats)
async def get_summary():
    """Return aggregated platform stats for the dashboard."""
    async with get_db_session() as db:
        # Reel counts by status
        total_reels = await db.scalar(select(func.count(Reel.id))) or 0
        completed = await db.scalar(
            select(func.count(Reel.id)).where(Reel.job_status == "completed")
        ) or 0
        failed = await db.scalar(
            select(func.count(Reel.id)).where(Reel.job_status == "failed")
        ) or 0
        pending = await db.scalar(
            select(func.count(Reel.id)).where(Reel.job_status == "pending")
        ) or 0

        # Follows and comments
        total_follows = await db.scalar(select(func.count(Follow.id))) or 0
        total_comments = await db.scalar(
            select(func.count(Reel.id)).where(Reel.comment_text.isnot(None))
        ) or 0

        # Links
        total_links = await db.scalar(select(func.count(ExtractedLink.id))) or 0

        # Account health
        accounts_active = await db.scalar(
            select(func.count(Account.id)).where(Account.status == "active")
        ) or 0
        accounts_quarantined = await db.scalar(
            select(func.count(Account.id)).where(Account.status == "quarantine")
        ) or 0

    return SummaryStats(
        total_reels_submitted=total_reels,
        reels_completed=completed,
        reels_failed=failed,
        reels_pending=pending,
        total_follows=total_follows,
        total_comments=total_comments,
        total_extracted_links=total_links,
        accounts_active=accounts_active,
        accounts_quarantined=accounts_quarantined,
    )
