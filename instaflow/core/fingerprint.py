"""
Device Fingerprint Manager — Module 1.2

Each Instagram account gets a **permanently assigned** device profile
generated once at registration time.  Consistency is critical:
Instagram detects device fingerprint changes and raises challenges.

The profile is stored inside the session vault and loaded into
instagrapi on every session start.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from instaflow.config.logging import get_logger
from instaflow.config.settings import get_settings

logger = get_logger(__name__)

# ── Curated device list (real-world Android devices) ───────────────────
# These are legitimate User-Agent strings from popular Android phones
# running recent Instagram versions.
DEVICE_CATALOG: list[dict[str, str]] = [
    {
        "manufacturer": "Samsung",
        "model": "SM-G998B",
        "android_version": "13",
        "android_release": "33",
        "dpi": "640dpi",
        "resolution": "1440x3200",
        "user_agent_device": "samsung; SM-G998B",
    },
    {
        "manufacturer": "Google",
        "model": "Pixel 7 Pro",
        "android_version": "14",
        "android_release": "34",
        "dpi": "560dpi",
        "resolution": "1440x3120",
        "user_agent_device": "google; Pixel 7 Pro",
    },
    {
        "manufacturer": "OnePlus",
        "model": "CPH2451",
        "android_version": "13",
        "android_release": "33",
        "dpi": "480dpi",
        "resolution": "1080x2400",
        "user_agent_device": "oneplus; CPH2451",
    },
    {
        "manufacturer": "Xiaomi",
        "model": "2201123G",
        "android_version": "13",
        "android_release": "33",
        "dpi": "440dpi",
        "resolution": "1080x2400",
        "user_agent_device": "xiaomi; 2201123G",
    },
    {
        "manufacturer": "Samsung",
        "model": "SM-S908B",
        "android_version": "14",
        "android_release": "34",
        "dpi": "640dpi",
        "resolution": "1440x3088",
        "user_agent_device": "samsung; SM-S908B",
    },
    {
        "manufacturer": "Samsung",
        "model": "SM-A546B",
        "android_version": "13",
        "android_release": "33",
        "dpi": "480dpi",
        "resolution": "1080x2340",
        "user_agent_device": "samsung; SM-A546B",
    },
    {
        "manufacturer": "Google",
        "model": "Pixel 8",
        "android_version": "14",
        "android_release": "34",
        "dpi": "420dpi",
        "resolution": "1080x2400",
        "user_agent_device": "google; Pixel 8",
    },
    {
        "manufacturer": "OnePlus",
        "model": "NE2215",
        "android_version": "13",
        "android_release": "33",
        "dpi": "560dpi",
        "resolution": "1440x3216",
        "user_agent_device": "oneplus; NE2215",
    },
]


def generate_fingerprint(account_id: int) -> dict[str, Any]:
    """
    Create a new, permanent device fingerprint for an account.

    Called exactly ONCE when an account is first registered.  The result
    is stored in the session vault and reused forever — never regenerated.
    """
    settings = get_settings()

    # Deterministic but varied device selection
    device = random.choice(DEVICE_CATALOG)
    app_version = settings.ig_app_version

    profile: dict[str, Any] = {
        "device_id": str(uuid.uuid4()),
        "phone_id": str(uuid.uuid4()),
        "uuid": str(uuid.uuid4()),
        "advertising_id": str(uuid.uuid4()),
        "device_manufacturer": device["manufacturer"],
        "device_model": device["model"],
        "android_version": device["android_version"],
        "android_release": device["android_release"],
        "dpi": device["dpi"],
        "resolution": device["resolution"],
        "app_version": app_version,
        "locale": settings.ig_locale,
        "timezone_offset": settings.ig_timezone_offset,
        "user_agent": (
            f"Instagram {app_version} Android "
            f"({device['android_release']}/{device['android_version']}; "
            f"{device['dpi']}; {device['resolution']}; "
            f"{device['user_agent_device']}; {device['model']}; "
            f"{device['model']}; qcom; {settings.ig_locale}; "
            f"{app_version.replace('.', '')})"
        ),
    }

    logger.info(
        "fingerprint.generated",
        account_id=account_id,
        device_model=device["model"],
        manufacturer=device["manufacturer"],
    )
    return profile


def apply_fingerprint_to_client(
    client: Any,  # instagrapi.Client
    fingerprint: dict[str, Any],
) -> None:
    """
    Apply a stored fingerprint profile to an instagrapi Client instance.

    Must be called BEFORE login or session load.
    """
    client.set_device(
        {
            "app_version": fingerprint["app_version"],
            "android_version": fingerprint["android_version"],
            "android_release": fingerprint["android_release"],
            "dpi": fingerprint["dpi"],
            "resolution": fingerprint["resolution"],
            "manufacturer": fingerprint["device_manufacturer"],
            "model": fingerprint["device_model"],
        }
    )
    client.set_user_agent(fingerprint["user_agent"])
    client.device_id = fingerprint["device_id"]
    client.phone_id = fingerprint["phone_id"]
    client.uuid = fingerprint["uuid"]

    logger.debug(
        "fingerprint.applied",
        device_model=fingerprint["device_model"],
    )
