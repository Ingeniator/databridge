from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg
from fastapi import Request

from databridge.config import get_settings


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=get_settings().database_url)


async def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool
