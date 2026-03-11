"""
Links API Routes — View and export extracted links.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from instaflow.config.logging import get_logger
from instaflow.config.settings import get_settings
from instaflow.storage.database import get_db_session
from instaflow.storage.models import ExtractedLink

logger = get_logger(__name__)
router = APIRouter()


class ExtractedLinkResponse(BaseModel):
    id: int
    original_url: Optional[str] = None
    final_url: Optional[str] = None
    redirect_chain: Optional[list] = None
    extraction_method: Optional[str] = None
    extracted_at: Optional[str] = None
    exported_to_excel: bool = False


@router.get("/extracted", response_model=list[ExtractedLinkResponse])
async def list_extracted_links(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    exported: Optional[bool] = Query(None, description="Filter by export status"),
):
    """List all extracted links with pagination."""
    async with get_db_session() as db:
        stmt = select(ExtractedLink).order_by(ExtractedLink.extracted_at.desc())

        if exported is not None:
            stmt = stmt.where(ExtractedLink.exported_to_excel == exported)

        stmt = stmt.limit(limit).offset(offset)
        result = await db.execute(stmt)
        links = result.scalars().all()

    return [
        ExtractedLinkResponse(
            id=link.id,
            original_url=link.original_url,
            final_url=link.final_url,
            redirect_chain=link.redirect_chain,
            extraction_method=link.extraction_method,
            extracted_at=link.extracted_at.isoformat() if link.extracted_at else None,
            exported_to_excel=link.exported_to_excel,
        )
        for link in links
    ]


@router.get("/export")
async def download_excel():
    """Download the latest daily Excel export file."""
    from datetime import datetime, timezone

    settings = get_settings()
    exports_dir = Path(settings.exports_dir)

    # Find the most recent export file
    files = sorted(exports_dir.glob("instaflow_export_*.xlsx"), reverse=True)

    if not files:
        # Try the cumulative file
        cumulative = exports_dir / "instaflow_ALL_TIME.xlsx"
        if cumulative.exists():
            return FileResponse(
                str(cumulative),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename="instaflow_ALL_TIME.xlsx",
            )
        return {"error": "No export files found. Run the export task first."}

    latest = files[0]
    return FileResponse(
        str(latest),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=latest.name,
    )
