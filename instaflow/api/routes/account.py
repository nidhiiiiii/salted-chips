"""
Account API Routes — Health, session status, and management.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from instaflow.config.logging import get_logger
from instaflow.core.health_monitor import HealthMonitor, OperationMode
from instaflow.storage.database import get_db_session
from instaflow.storage.models import Account

logger = get_logger(__name__)
router = APIRouter()


class AccountHealthResponse(BaseModel):
    account_id: int
    ig_username: str
    health_score: int
    mode: str
    status: str


class AccountSessionResponse(BaseModel):
    account_id: int
    ig_username: str
    session_valid: bool
    vault_exists: bool


@router.get("/health", response_model=list[AccountHealthResponse])
async def get_account_health():
    """Return health score and operational mode for all accounts."""
    async with get_db_session() as db:
        result = await db.execute(select(Account).order_by(Account.id))
        accounts = result.scalars().all()

    return [
        AccountHealthResponse(
            account_id=a.id,
            ig_username=a.ig_username,
            health_score=a.health_score,
            mode=HealthMonitor.get_mode(a.health_score).value,
            status=a.status,
        )
        for a in accounts
    ]


@router.get("/session", response_model=AccountSessionResponse)
async def check_session(account_id: int = 1):
    """Check if an account's session vault exists and is valid."""
    from instaflow.core.session_vault import SessionVault

    async with get_db_session() as db:
        result = await db.execute(
            select(Account).where(Account.id == account_id)
        )
        account = result.scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    vault = SessionVault(account_id, account.ig_username)
    vault_exists = vault.exists

    session_valid = False
    if vault_exists:
        try:
            data = vault.load()
            session_valid = bool(data.get("instagrapi_settings"))
        except Exception:
            pass

    return AccountSessionResponse(
        account_id=account_id,
        ig_username=account.ig_username,
        session_valid=session_valid,
        vault_exists=vault_exists,
    )
