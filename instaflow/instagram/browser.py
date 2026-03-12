"""
Playwright Browser Session — Module 5.3 (Mode B)

Handles link extraction when the DM contains in-app buttons or
story links that can't be resolved via simple HTTP redirects.

Uses persistent browser context per account to maintain session cookies.
Network interception captures the full redirect chain.
"""

import asyncio
from __future__ import annotations

from typing import Any

from instaflow.config.logging import get_logger

logger = get_logger(__name__)


class BrowserLinkExtractor:
    """
    Playwright-based link extraction for complex CTA buttons.

    Used for ~20% of cases where HTTP redirect resolution fails
    (in-app links, story reply buttons, etc.).

    Usage::

        extractor = BrowserLinkExtractor()
        result = await extractor.extract("https://instagram.com/...", proxy_url)
    """

    async def extract(
        self,
        target_url: str,
        proxy_url: str | None = None,
    ) -> dict[str, Any]:
        """
        Launch a headless browser, navigate to `target_url`, and capture
        the final URL after all redirects.

        Returns:
            {
                "final_url": str,
                "redirect_chain": list[str],
                "method": "playwright",
            }
        """
        from playwright.async_api import async_playwright

        redirect_chain: list[str] = [target_url]

        try:
            async with async_playwright() as p:
                browser_args: dict[str, Any] = {"headless": True}
                if proxy_url:
                    browser_args["proxy"] = {"server": proxy_url}

                browser = await p.chromium.launch(**browser_args)
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Linux; Android 13; SM-G998B) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.6099.144 Mobile Safari/537.36"
                    ),
                    viewport={"width": 412, "height": 915},
                    is_mobile=True,
                )

                page = await context.new_page()

                # Intercept network requests to capture redirect chain
                async def on_response(response: Any) -> None:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if location:
                            redirect_chain.append(location)

                page.on("response", on_response)

                # Navigate with generous timeout
                await page.goto(target_url, wait_until="networkidle", timeout=30000)

                # Capture final URL
                final_url = page.url
                if final_url not in redirect_chain:
                    redirect_chain.append(final_url)

                await browser.close()

                logger.info(
                    "browser.extraction_complete",
                    target_url=target_url,
                    final_url=final_url,
                    hops=len(redirect_chain),
                )

                return {
                    "final_url": final_url,
                    "redirect_chain": redirect_chain,
                    "method": "playwright",
                }

        except Exception as exc:
            logger.exception(
                "browser.extraction_failed",
                target_url=target_url,
                error=str(exc),
            )
            return {
                "final_url": target_url,
                "redirect_chain": redirect_chain,
                "method": "playwright",
                "error": str(exc),
            }
