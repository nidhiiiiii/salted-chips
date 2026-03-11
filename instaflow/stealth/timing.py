"""
Behavioral Timing Engine — Module 2.1

All inter-action delays use a **log-normal distribution**.
Humans are NOT uniformly random — log-normal produces realistic
short delays with occasional longer pauses, mimicking natural browsing.

Usage::

    from instaflow.stealth.timing import Delay
    await Delay.before_follow()
    await Delay.before_comment()
"""

from __future__ import annotations

import asyncio
import math
import random

from instaflow.config.logging import get_logger

logger = get_logger(__name__)


def _lognormal_delay(mean: float, sigma: float, floor: float = 1.0) -> float:
    """
    Sample a delay from a log-normal distribution.

    Parameters
    ----------
    mean   : Desired mean delay in seconds.
    sigma  : Standard deviation (shape) of the underlying normal distribution.
    floor  : Minimum returned value (seconds).
    """
    # Convert mean/sigma of the *log-normal* to µ/σ of the *underlying* normal.
    mu = math.log(mean**2 / math.sqrt(sigma**2 + mean**2))
    s = math.sqrt(math.log(1 + (sigma**2 / mean**2)))

    delay = random.lognormvariate(mu, s)
    return max(delay, floor)


class Delay:
    """
    Pre-configured delay profiles matching human browsing patterns.

    Every method is an async coroutine that sleeps for a sampled duration,
    logs the delay, and then returns the actual sleep time.
    """

    @staticmethod
    async def before_follow() -> float:
        """Simulate 'viewing reel' before clicking follow — 3–8 s typical."""
        t = _lognormal_delay(mean=5.5, sigma=1.5, floor=2.0)
        logger.debug("delay.before_follow", seconds=round(t, 2))
        await asyncio.sleep(t)
        return t

    @staticmethod
    async def before_comment() -> float:
        """Simulate 'watching reel' before commenting — 8–20 s typical."""
        t = _lognormal_delay(mean=14.0, sigma=4.0, floor=5.0)
        logger.debug("delay.before_comment", seconds=round(t, 2))
        await asyncio.sleep(t)
        return t

    @staticmethod
    async def between_reels() -> float:
        """Cool-down between processing two reels — 45–180 s typical."""
        t = _lognormal_delay(mean=90.0, sigma=30.0, floor=30.0)
        logger.debug("delay.between_reels", seconds=round(t, 2))
        await asyncio.sleep(t)
        return t

    @staticmethod
    async def dm_poll_interval() -> float:
        """Pause between DM poll cycles — 4–7 min typical."""
        t = _lognormal_delay(mean=330.0, sigma=60.0, floor=180.0)
        logger.debug("delay.dm_poll", seconds=round(t, 2))
        await asyncio.sleep(t)
        return t

    @staticmethod
    async def session_warmup() -> float:
        """Initial idle period when a session starts — 60–120 s."""
        t = random.uniform(60.0, 120.0)
        logger.debug("delay.session_warmup", seconds=round(t, 2))
        await asyncio.sleep(t)
        return t

    @staticmethod
    async def before_like() -> float:
        """Brief pause before an optional like — 2–5 s."""
        t = _lognormal_delay(mean=3.5, sigma=1.0, floor=1.5)
        logger.debug("delay.before_like", seconds=round(t, 2))
        await asyncio.sleep(t)
        return t

    @staticmethod
    async def jitter(min_s: float = 0.5, max_s: float = 2.0) -> float:
        """Tiny random jitter for anti-pattern purposes."""
        t = random.uniform(min_s, max_s)
        await asyncio.sleep(t)
        return t
