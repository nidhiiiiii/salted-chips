"""
Proxy Manager — Module 2.4

Manages a pool of residential proxies stored in PostgreSQL.

Key rules:
  • Each account gets ONE sticky proxy per session.
  • Proxy must be from the same country as the account's registration.
  • Proxy is health-checked before assignment.
  • Lifecycle: active → cooling → degraded → retired.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from instaflow.config.logging import get_logger

logger = get_logger(__name__)


class ProxyManager:
    """
    Proxy pool manager backed by PostgreSQL.

    Usage::

        manager = ProxyManager(db_session)
        proxy = await manager.acquire("IN")  # India
        # ... use proxy ...
        await manager.release(proxy["id"])
    """

    def __init__(self, db_session: AsyncSession) -> None:
        self._db = db_session

    async def acquire(
        self,
        country: str,
        exclude_ids: Optional[list[int]] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Find and lock a healthy proxy for the given country.

        Returns proxy dict or None if no proxy is available.
        """
        from instaflow.storage.models import Proxy

        exclude_ids = exclude_ids or []

        stmt = (
            select(Proxy)
            .where(
                Proxy.country == country.upper(),
                Proxy.status.in_(["active", "degraded"]),
                Proxy.id.notin_(exclude_ids),
            )
            .order_by(
                # Prefer active over degraded, then least recently used
                Proxy.status.asc(),
                Proxy.last_used_at.asc().nulls_first(),
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )

        result = await self._db.execute(stmt)
        proxy = result.scalar_one_or_none()

        if proxy is None:
            logger.warning("proxy.none_available", country=country)
            return None

        # Health-check before handing out
        healthy = await self._health_check(proxy)
        if not healthy:
            await self._mark_degraded(proxy.id)
            # Retry once with this proxy excluded
            return await self.acquire(country, exclude_ids=[*(exclude_ids), proxy.id])

        # Mark as in-use
        proxy.last_used_at = datetime.now(tz=timezone.utc)
        await self._db.commit()

        logger.info(
            "proxy.acquired",
            proxy_id=proxy.id,
            host=proxy.host,
            country=proxy.country,
        )

        return {
            "id": proxy.id,
            "host": proxy.host,
            "port": proxy.port,
            "username": proxy.username,
            "password": proxy.password,
            "url": f"http://{proxy.username}:{proxy.password}@{proxy.host}:{proxy.port}",
        }

    async def release(self, proxy_id: int, failed: bool = False) -> None:
        """Release a proxy back to the pool, optionally marking it degraded."""
        from instaflow.storage.models import Proxy

        new_status = "degraded" if failed else "cooling"
        stmt = (
            update(Proxy)
            .where(Proxy.id == proxy_id)
            .values(
                status=new_status,
                last_used_at=datetime.now(tz=timezone.utc),
            )
        )
        await self._db.execute(stmt)
        await self._db.commit()

        logger.info("proxy.released", proxy_id=proxy_id, status=new_status)

    async def recover_cooling(self, cool_down_minutes: int = 30) -> int:
        """
        Move proxies from 'cooling' back to 'active' if they've rested
        long enough.  Called periodically by a maintenance task.
        Returns the count of recovered proxies.
        """
        from instaflow.storage.models import Proxy

        cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=cool_down_minutes)
        stmt = (
            update(Proxy)
            .where(Proxy.status == "cooling", Proxy.last_used_at < cutoff)
            .values(status="active")
            .returning(Proxy.id)
        )
        result = await self._db.execute(stmt)
        recovered = result.fetchall()
        await self._db.commit()

        count = len(recovered)
        if count:
            logger.info("proxy.recovered", count=count)
        return count

    async def retire(self, proxy_id: int) -> None:
        """Permanently remove a proxy from the active pool."""
        from instaflow.storage.models import Proxy

        stmt = update(Proxy).where(Proxy.id == proxy_id).values(status="retired")
        await self._db.execute(stmt)
        await self._db.commit()
        logger.info("proxy.retired", proxy_id=proxy_id)

    # ── Private ────────────────────────────────────────────────────────

    async def _health_check(self, proxy: Any) -> bool:
        """Lightweight HTTP check to verify proxy connectivity."""
        import httpx

        proxy_url = f"http://{proxy.username}:{proxy.password}@{proxy.host}:{proxy.port}"
        try:
            async with httpx.AsyncClient(
                proxies={"http://": proxy_url, "https://": proxy_url},
                timeout=10,
            ) as client:
                resp = await client.get("https://httpbin.org/ip")
                return resp.status_code == 200
        except Exception:
            logger.warning("proxy.health_check_failed", proxy_id=proxy.id)
            return False

    async def _mark_degraded(self, proxy_id: int) -> None:
        from instaflow.storage.models import Proxy

        stmt = update(Proxy).where(Proxy.id == proxy_id).values(status="degraded")
        await self._db.execute(stmt)
        await self._db.commit()
