"""
FastAPI Application — Module 7

Control plane API server with:
  • REST endpoints for reel submission, monitoring, and control
  • WebSocket live event feed
  • Lifespan hooks for DB/Redis startup and shutdown
  • CORS middleware for dashboard integration
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from instaflow.config.logging import get_logger, setup_logging
from instaflow.config.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown hooks."""
    # ── Startup ────────────────────────────────────────────────
    setup_logging()
    logger = get_logger(__name__)
    settings = get_settings()
    logger.info("api.startup", app_env=settings.app_env)

    from instaflow.storage.database import init_db
    from instaflow.storage.redis_client import get_redis

    # In dev mode, warn but don't crash if infrastructure isn't up yet.
    # In production these must be available — the container healthchecks
    # in docker-compose.yml enforce this before the API container starts.
    try:
        await init_db()
        logger.info("api.database_ready")
    except Exception as exc:
        msg = f"PostgreSQL not reachable: {exc}"
        if settings.app_env == "production":
            raise RuntimeError(msg) from exc
        logger.warning("api.database_unavailable", detail=msg)

    try:
        await get_redis()
        logger.info("api.redis_ready")
    except Exception as exc:
        msg = f"Redis not reachable: {exc}"
        if settings.app_env == "production":
            raise RuntimeError(msg) from exc
        logger.warning("api.redis_unavailable", detail=msg)

    yield

    # ── Shutdown ───────────────────────────────────────────────
    from instaflow.storage.database import close_db
    from instaflow.storage.redis_client import close_redis

    await close_redis()
    await close_db()
    logger.info("api.shutdown")


def create_app() -> FastAPI:
    """Application factory — returns a fully configured FastAPI instance."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=(
            "InstaFlow Automation Engine — Automated reel engagement "
            "with DM CTA extraction and Excel reporting."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS — allow dashboard frontends
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Tighten in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Register Routes ────────────────────────────────────────
    from instaflow.api.routes.account import router as account_router
    from instaflow.api.routes.control import router as control_router
    from instaflow.api.routes.links import router as links_router
    from instaflow.api.routes.reels import router as reels_router
    from instaflow.api.routes.stats import router as stats_router
    from instaflow.api.websocket import router as ws_router

    app.include_router(reels_router, prefix="/api/reels", tags=["Reels"])
    app.include_router(account_router, prefix="/api/account", tags=["Account"])
    app.include_router(links_router, prefix="/api/links", tags=["Links"])
    app.include_router(control_router, prefix="/api/control", tags=["Control"])
    app.include_router(stats_router, prefix="/api/stats", tags=["Stats"])
    app.include_router(ws_router, tags=["WebSocket"])

    @app.get("/health", tags=["System"])
    async def health():
        return {"status": "ok", "service": settings.app_name}

    return app


# For `uvicorn instaflow.api.main:app`
app = create_app()
