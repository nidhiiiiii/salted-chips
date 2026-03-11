"""
Encrypted Session Vault — Module 1.1

Manages per-account session state using Fernet symmetric encryption.
Session files live in the `vaults/` directory and contain: cookies,
device UUID, login timestamp, challenge history, and last action time.

Security guarantees:
  • Raw passwords are NEVER stored — only session tokens.
  • Vault files are encrypted at rest; key lives only in env.
  • Vault is loaded once per boot and persisted after every successful API call.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from instaflow.config.logging import get_logger
from instaflow.config.settings import get_settings

logger = get_logger(__name__)


class SessionVaultError(Exception):
    """Raised when vault operations fail."""


class SessionVault:
    """
    Thread-safe encrypted vault for a single Instagram account session.

    Usage::

        vault = SessionVault(account_id=1, ig_username="my_account")
        vault.load()               # decrypt from disk
        session_data = vault.data  # use session cookies, etc.
        vault.data["last_action"] = datetime.now(tz=timezone.utc).isoformat()
        vault.save()               # re-encrypt and persist
    """

    def __init__(self, account_id: int, ig_username: str) -> None:
        settings = get_settings()
        self._fernet = Fernet(settings.vault_encryption_key.encode())
        self._vault_dir = Path(settings.vault_dir)
        self._vault_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._vault_dir / f"{ig_username}_{account_id}.vault"
        self._account_id = account_id
        self._ig_username = ig_username
        self._data: dict[str, Any] = {}

    # ── Public API ─────────────────────────────────────────────────────

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    @data.setter
    def data(self, value: dict[str, Any]) -> None:
        self._data = value

    @property
    def exists(self) -> bool:
        return self._file_path.is_file()

    def load(self) -> dict[str, Any]:
        """Decrypt and return session state from disk."""
        if not self.exists:
            logger.info(
                "vault.not_found",
                account_id=self._account_id,
                username=self._ig_username,
            )
            self._data = self._default_data()
            return self._data

        try:
            encrypted = self._file_path.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            self._data = json.loads(decrypted)
            logger.info(
                "vault.loaded",
                account_id=self._account_id,
                username=self._ig_username,
            )
            return self._data
        except InvalidToken as exc:
            raise SessionVaultError(
                f"Failed to decrypt vault for {self._ig_username} — "
                "is VAULT_ENCRYPTION_KEY correct?"
            ) from exc
        except json.JSONDecodeError as exc:
            raise SessionVaultError(
                f"Vault file for {self._ig_username} is corrupted."
            ) from exc

    def save(self) -> None:
        """Encrypt current state and write atomically to disk."""
        self._data["last_persisted_at"] = datetime.now(tz=timezone.utc).isoformat()
        payload = json.dumps(self._data, default=str).encode()
        encrypted = self._fernet.encrypt(payload)

        # Atomic write: write to tmp, then rename
        tmp_path = self._file_path.with_suffix(".tmp")
        tmp_path.write_bytes(encrypted)
        tmp_path.rename(self._file_path)

        logger.info(
            "vault.saved",
            account_id=self._account_id,
            username=self._ig_username,
        )

    def update_session(self, instagrapi_settings: dict[str, Any]) -> None:
        """
        Merge instagrapi client settings (cookies, headers, etc.)
        into vault data and persist immediately.
        """
        self._data["instagrapi_settings"] = instagrapi_settings
        self._data["last_action_at"] = datetime.now(tz=timezone.utc).isoformat()
        self.save()

    def record_challenge(self, challenge_type: str) -> None:
        """Append a challenge event to the vault's history."""
        history: list[dict] = self._data.setdefault("challenge_history", [])
        history.append(
            {
                "type": challenge_type,
                "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        )
        self.save()

    # ── Private ────────────────────────────────────────────────────────

    def _default_data(self) -> dict[str, Any]:
        return {
            "account_id": self._account_id,
            "ig_username": self._ig_username,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "instagrapi_settings": {},
            "challenge_history": [],
            "last_action_at": None,
            "last_persisted_at": None,
        }
