"""
Comment Engine — Module 2.2 (Simplified)

The ONLY comment this tool ever posts is: **"link"**

Creators set up auto-DM funnels that fire when someone comments
a trigger keyword.  "link" is the near-universal trigger.

This module reads the comment text from `comments.yaml` so an operator
can reconfigure it without touching code, but the default and expected
value is always just `"link"`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from instaflow.config.logging import get_logger
from instaflow.config.settings import get_settings

logger = get_logger(__name__)

_cached_config: dict[str, Any] | None = None


def _load_config() -> dict[str, Any]:
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    settings = get_settings()
    config_path = Path(settings.comments_yaml)

    if not config_path.exists():
        logger.warning("comment_engine.config_missing, using default 'link'")
        _cached_config = {"comment_text": "link"}
        return _cached_config

    with open(config_path, "r") as f:
        _cached_config = yaml.safe_load(f)
    return _cached_config


def get_comment_text() -> str:
    """
    Return the comment text to post on reels.

    Always returns the configured string (default: "link").
    No variation, no templates — just the trigger keyword.
    """
    config = _load_config()
    text = config.get("comment_text", "link")
    logger.debug("comment_engine.text", text=text)
    return text


def reload_config() -> None:
    """Force-reload comment config from disk (useful after hot-edit)."""
    global _cached_config
    _cached_config = None
    _load_config()
