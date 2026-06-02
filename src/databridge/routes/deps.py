from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import Depends, HTTPException

from databridge.auth import AuthContext, get_auth
from databridge.config import SystemSourceConfig, get_settings
from databridge.db.pool import get_pool


async def get_connection_or_404(
    id: UUID,
    pool: asyncpg.Pool = Depends(get_pool),
    auth: AuthContext = Depends(get_auth),
) -> asyncpg.Record:
    row = await pool.fetchrow(
        "SELECT * FROM connections WHERE id = $1 AND owner_key = $2",
        id,
        auth.public_key,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="connection not found")
    return row


def get_system_sources() -> list[SystemSourceConfig]:
    return list(get_settings().datasources)


async def get_arq_pool(request):
    return getattr(request.app.state, "arq_pool", None)
