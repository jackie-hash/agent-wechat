"""API Key authentication."""

import hashlib
from datetime import datetime, timezone

from fastapi import Request, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import database
from .models import APIKey


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def validate_api_key(api_key: str) -> str | None:
    """Validate an API key and return the associated agent_id, or None if invalid."""
    if not api_key:
        return None

    key_hash = hash_key(api_key)

    async with database.async_session_factory() as session:
        result = await session.execute(
            select(APIKey).where(
                APIKey.key_hash == key_hash,
                APIKey.is_active == True,
            )
        )
        api_key_record = result.scalar_one_or_none()

        if api_key_record is None:
            return None

        # Check if within 5-minute grace period after revocation
        if api_key_record.revoked_at:
            grace_end = api_key_record.revoked_at.timestamp() + 300
            if datetime.now(timezone.utc).timestamp() > grace_end:
                return None

        return api_key_record.agent_id


async def require_auth(request: Request) -> str:
    """FastAPI dependency: extract and validate API key, return agent_id."""
    api_key = request.headers.get("X-API-Key", "")

    # Also support query param for SSE connections
    if not api_key:
        api_key = request.query_params.get("api_key", "")

    # Check master key first
    master_key = request.app.state.config.master_api_key
    if api_key == master_key:
        return "master"

    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    agent_id = await validate_api_key(api_key)
    if agent_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired API key")

    return agent_id


async def require_master(request: Request) -> str:
    """FastAPI dependency: require master API key."""
    api_key = request.headers.get("X-API-Key", "")
    if not api_key:
        api_key = request.query_params.get("api_key", "")

    master_key = request.app.state.config.master_api_key
    if api_key != master_key:
        raise HTTPException(status_code=403, detail="Master API key required")

    return "master"
