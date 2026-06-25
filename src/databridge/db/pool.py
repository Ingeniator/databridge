from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg
from fastapi import Request

from databridge.config import get_settings


async def create_pool() -> asyncpg.Pool:
    s = get_settings()
    return await asyncpg.create_pool(dsn=s.database_url, max_size=s.db_pool_max_size)


async def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool
