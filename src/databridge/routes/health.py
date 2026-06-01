from __future__ import annotations

import asyncio

import asyncpg
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from databridge.config import get_settings
from databridge.db.pool import get_pool

router = APIRouter(tags=["health"])

VERSION = "0.1.0"


async def _ping_db(pool: asyncpg.Pool) -> tuple[str, str, str | None]:
    try:
        await pool.fetchval("SELECT 1")
        return "db", "ok", None
    except Exception as exc:
        return "db", "degraded", str(exc)


async def _ping_source(name: str, pool_or_none) -> tuple[str, str, str | None]:
    # placeholder — adapters implement real ping in T030/T031
    return name, "ok", None


async def _run_checks(pool: asyncpg.Pool) -> dict[str, tuple[str, str | None]]:
    tasks = [_ping_db(pool)]
    for ds in get_settings().datasources:
        tasks.append(_ping_source(ds.name, pool))
    results = await asyncio.gather(*tasks)
    return {name: (state, detail) for name, state, detail in results}


@router.get("/livez")
async def livez():
    return {"status": "ok"}


@router.get("/ready")
async def ready(pool: asyncpg.Pool = Depends(get_pool)):
    checks = await _run_checks(pool)
    components = {name: state for name, (state, _) in checks.items()}
    overall = "degraded" if any(s == "degraded" for s in components.values()) else "ok"
    status_code = 503 if overall == "degraded" else 200
    return JSONResponse(
        content={"status": overall, "components": components},
        status_code=status_code,
    )


@router.get("/api/v1/health")
async def health(pool: asyncpg.Pool = Depends(get_pool)):
    checks = await _run_checks(pool)
    components = {name: state for name, (state, _) in checks.items()}
    details = {name: detail for name, (state, detail) in checks.items() if state == "degraded" and detail}
    overall = "degraded" if any(s == "degraded" for s in components.values()) else "ok"
    return {
        "status": overall,
        "version": VERSION,
        "components": components,
        "details": details or None,
    }
