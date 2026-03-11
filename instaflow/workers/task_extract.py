"""
Link Extraction Task — Module 4.2

Resolves CTA URLs from DMs through two modes:
  Mode A (80%): HTTP redirect chasing via httpx
  Mode B (20%): Playwright browser for in-app links

Saves the final URL + full redirect chain to PostgreSQL and
triggers Excel export.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

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
    name="instaflow.workers.task_extract.extract_link",
    max_retries=3,
    default_retry_delay=30,
    queue="critical",
    acks_late=True,
)
def extract_link(
    self,
    dm_message_id: int,
    raw_url: str,
    account_id: int,
    creator_user_id: int,
) -> dict:
    """
    Resolve a CTA URL to its final destination.
    Tries HTTP redirect chasing first, falls back to Playwright.
    """
    return _run_async(
        _extract_link_async(self, dm_message_id, raw_url, account_id, creator_user_id)
    )


async def _extract_link_async(
    task,
    dm_message_id: int,
    raw_url: str,
    account_id: int,
    creator_user_id: int,
) -> dict:
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import ExtractedLink

    logger.info(
        "task.extract.start",
        raw_url=raw_url,
        dm_message_id=dm_message_id,
    )

    # Try Mode A first: HTTP redirect resolution
    result = await _resolve_via_http(raw_url)

    # If HTTP resolution fails or gets blocked, try Playwright
    if result.get("error") or result["final_url"] == raw_url:
        logger.info("task.extract.fallback_to_playwright", raw_url=raw_url)
        result = await _resolve_via_playwright(raw_url)

    # Save to database
    async with get_db_session() as db:
        link = ExtractedLink(
            dm_message_id=dm_message_id,
            original_url=raw_url,
            redirect_chain=result["redirect_chain"],
            final_url=result["final_url"],
            extraction_method=result["method"],
            extracted_at=datetime.now(tz=timezone.utc),
            exported_to_excel=False,
        )
        db.add(link)

    logger.info(
        "task.extract.completed",
        final_url=result["final_url"],
        method=result["method"],
        hops=len(result["redirect_chain"]),
    )

    return {
        "status": "extracted",
        "original_url": raw_url,
        "final_url": result["final_url"],
        "redirect_chain": result["redirect_chain"],
        "method": result["method"],
    }


async def _resolve_via_http(url: str) -> dict[str, Any]:
    """
    Mode A: Chase HTTP redirects using httpx.
    Follows up to 15 redirects, captures the full chain.
    """
    import httpx

    redirect_chain: list[str] = [url]

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=15,
            timeout=15.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 13; SM-G998B) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.6099.144 Mobile Safari/537.36"
                ),
            },
        ) as client:
            response = await client.get(url)

            # Build redirect chain from history
            for r in response.history:
                location = str(r.url)
                if location not in redirect_chain:
                    redirect_chain.append(location)

            final_url = str(response.url)
            if final_url not in redirect_chain:
                redirect_chain.append(final_url)

            return {
                "final_url": final_url,
                "redirect_chain": redirect_chain,
                "method": "requests",
            }

    except Exception as exc:
        logger.warning("extract.http_failed", url=url, error=str(exc))
        return {
            "final_url": url,
            "redirect_chain": redirect_chain,
            "method": "requests",
            "error": str(exc),
        }


async def _resolve_via_playwright(url: str) -> dict[str, Any]:
    """Mode B: Browser-based link extraction for in-app/complex CTAs."""
    from instaflow.instagram.browser import BrowserLinkExtractor

    extractor = BrowserLinkExtractor()
    return await extractor.extract(url)
