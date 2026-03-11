"""
Instagram Client Wrapper — Module (Instagram Interface Layer)

Wraps instagrapi.Client with:
  • Automatic session vault load/save
  • Device fingerprint application
  • Proxy assignment
  • Challenge callback wiring
  • Health score integration

This is the SINGLE entry point for all Instagram API interactions.
No other module should instantiate instagrapi.Client directly.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from instagrapi import Client as InstaClient
from instagrapi.exceptions import (
    ChallengeRequired,
    LoginRequired,
    RateLimitError,
)

from instaflow.config.logging import get_logger
from instaflow.config.settings import get_settings
from instaflow.core.fingerprint import apply_fingerprint_to_client, generate_fingerprint
from instaflow.core.health_monitor import HealthMonitor
from instaflow.core.session_vault import SessionVault

logger = get_logger(__name__)


class InstagramClient:
    """
    Managed Instagram client with vault-backed sessions.

    Usage::

        client = InstagramClient(account_id=1, ig_username="bot_account")
        await client.initialize()
        user_info = client.api.user_info_by_username("target_user")
        await client.persist_session()
    """

    def __init__(
        self,
        account_id: int,
        ig_username: str,
        ig_password: Optional[str] = None,
        proxy_url: Optional[str] = None,
    ) -> None:
        self._account_id = account_id
        self._ig_username = ig_username
        self._ig_password = ig_password
        self._proxy_url = proxy_url
        self._vault = SessionVault(account_id, ig_username)
        self._api = InstaClient()
        self._initialized = False

    @property
    def api(self) -> InstaClient:
        """Direct access to the underlying instagrapi client."""
        if not self._initialized:
            raise RuntimeError(
                "Client not initialized. Call `await client.initialize()` first."
            )
        return self._api

    @property
    def account_id(self) -> int:
        return self._account_id

    async def initialize(self) -> None:
        """
        Boot sequence:
          1. Load session vault
          2. Apply device fingerprint
          3. Set proxy
          4. Restore session OR fresh login
          5. Persist updated session
        """
        # Load or create vault
        vault_data = self._vault.load()

        # Apply or generate fingerprint
        fingerprint = vault_data.get("fingerprint")
        if not fingerprint:
            fingerprint = generate_fingerprint(self._account_id)
            vault_data["fingerprint"] = fingerprint
            self._vault.save()

        apply_fingerprint_to_client(self._api, fingerprint)

        # Set proxy
        if self._proxy_url:
            self._api.set_proxy(self._proxy_url)
            logger.info("client.proxy_set", account_id=self._account_id)

        # Restore session from vault or perform fresh login
        ig_settings = vault_data.get("instagrapi_settings")
        if ig_settings:
            try:
                self._api.set_settings(ig_settings)
                self._api.login(self._ig_username, self._ig_password or "", relogin=False)
                logger.info("client.session_restored", account_id=self._account_id)
            except (LoginRequired, ChallengeRequired):
                logger.warning(
                    "client.session_expired",
                    account_id=self._account_id,
                )
                await self._fresh_login()
        else:
            await self._fresh_login()

        self._initialized = True
        await self.persist_session()

    async def persist_session(self) -> None:
        """Save current instagrapi state back to the encrypted vault."""
        settings = self._api.get_settings()
        self._vault.update_session(settings)

    async def safe_call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """
        Execute an instagrapi method with error handling and health tracking.

        Wraps the call in a thread executor (instagrapi is synchronous)
        and translates exceptions into health signals.
        """
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, lambda: func(*args, **kwargs))
            return result
        except RateLimitError:
            logger.warning("client.rate_limited", account_id=self._account_id)
            raise
        except ChallengeRequired:
            logger.warning("client.challenge_required", account_id=self._account_id)
            self._vault.record_challenge("checkpoint")
            raise
        except LoginRequired:
            logger.error("client.login_required", account_id=self._account_id)
            raise
        except Exception:
            logger.exception("client.unexpected_error", account_id=self._account_id)
            raise
        finally:
            # Always persist session after any API call
            await self.persist_session()

    async def _fresh_login(self) -> None:
        """Perform a fresh login (blocking, run in executor)."""
        if not self._ig_password:
            raise RuntimeError(
                f"No password provided for {self._ig_username} and no valid session exists."
            )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._api.login(self._ig_username, self._ig_password),
        )
        logger.info("client.fresh_login", account_id=self._account_id)

    async def close(self) -> None:
        """Persist session and clean up."""
        if self._initialized:
            await self.persist_session()
            self._initialized = False
            logger.info("client.closed", account_id=self._account_id)
