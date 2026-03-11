"""
Structured logging configuration using structlog.

Every log line is emitted as JSON with standard context fields
(timestamp, account_id, task_id, etc.) for easy ingestion by
log aggregation tools (ELK, Loki, CloudWatch).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog
from structlog.types import Processor

from instaflow.config.settings import get_settings


def setup_logging() -> None:
    """
    Call once at application startup (FastAPI lifespan, worker boot, CLI).

    Sets up:
      1. structlog processors for JSON output
      2. stdlib root logger bridged through structlog
      3. Rotating file handler for persistent logs
    """
    settings = get_settings()
    log_dir = Path(settings.logs_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.debug:
        # Pretty console output in dev
        renderer: Processor = structlog.dev.ConsoleRenderer()
    else:
        # Machine-readable JSON in production
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # Rotating file handler
    from logging.handlers import RotatingFileHandler

    file_handler = RotatingFileHandler(
        filename=str(log_dir / "instaflow.log"),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(getattr(logging, settings.log_level.upper()))

    # Quiet noisy libraries
    for name in ("uvicorn.access", "httpx", "httpcore", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structured logger, optionally scoped to *name*."""
    return structlog.get_logger(name)
