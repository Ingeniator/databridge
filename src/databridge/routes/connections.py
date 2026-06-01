from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from databridge.adapters import get_adapter
from databridge.auth import AuthContext, get_auth
from databridge.config import SystemSourceConfig, get_settings
from databridge.crypto import decrypt_credentials, encrypt_credentials
from databridge.db.connections import (
    count_referencing_jobs,
    delete_connection,
    get_connection,
    insert_connection,
    list_connections,
    update_connection,
    update_connection_status,
)
from databridge.db.pool import get_pool
from databridge.models import (
    ConnectionCreate,
    ConnectionListResponse,
    ConnectionPatch,
    ConnectionResponse,
    ConnectionTestRequest,
    PingResponse,
    PreviewRequest,
    PreviewResponse,
    SchemaResponse,
)
from databridge.routes.deps import get_connection_or_404, get_system_sources

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["connections"])


def _row_to_response(row, system: bool = False) -> ConnectionResponse:
    return ConnectionResponse(
        id=row["id"],
        label=row["label"],
        type=row["type"],
        role=row["role"],
        connection_url=row["connection_url"],
        status=row["status"],
        system=system,
        last_tested_at=row.get("last_tested_at"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _system_source_to_response(src: SystemSourceConfig) -> ConnectionResponse:
    return ConnectionResponse(
        id=src.id,
        label=src.name,
        type=src.type,
        role="source",
        connection_url=src.url or src.endpoint or "",
        status="untested",
        system=True,
    )


def _creds_to_dict(credentials) -> dict:
    if hasattr(credentials, "model_dump"):
        return credentials.model_dump()
    return dict(credentials)


# ── Pre-save connection test ──────────────────────────────────────────────────

@router.post("/connections/test", response_model=PingResponse)
async def test_connection(
    body: ConnectionTestRequest,
    auth: AuthContext = Depends(get_auth),
) -> PingResponse:
    from dataclasses import dataclass

    @dataclass
    class _SyntheticConn:
        type: str
        connection_url: str

    conn = _SyntheticConn(type=body.type, connection_url=body.connection_url)
    creds = _creds_to_dict(body.credentials)
    adapter = get_adapter(conn, creds)

    t0 = time.perf_counter()
    try:
        await adapter.ping()
        return PingResponse(status="reachable", latency_ms=round((time.perf_counter() - t0) * 1000, 1))
    except Exception as exc:
        return PingResponse(status="unreachable", error=str(exc))


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.post("/connections", status_code=201, response_model=ConnectionResponse)
async def create_connection(
    body: ConnectionCreate,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    system_sources: list[SystemSourceConfig] = Depends(get_system_sources),
) -> ConnectionResponse:
    creds_enc = encrypt_credentials(_creds_to_dict(body.credentials))
    row = await insert_connection(
        pool,
        owner_key=auth.public_key,
        label=body.label,
        type=body.type,
        role=body.role,
        connection_url=body.connection_url,
        credentials_enc=creds_enc,
    )
    return _row_to_response(row)


@router.get("/connections", response_model=ConnectionListResponse)
async def list_user_connections(
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    system_sources: list[SystemSourceConfig] = Depends(get_system_sources),
) -> ConnectionListResponse:
    rows = await list_connections(pool, owner_key=auth.public_key)
    items = [_row_to_response(r) for r in rows]
    items += [_system_source_to_response(s) for s in system_sources]
    return ConnectionListResponse(items=items)


@router.get("/connections/{id}", response_model=ConnectionResponse)
async def get_one_connection(
    row: asyncpg.Record = Depends(get_connection_or_404),
) -> ConnectionResponse:
    return _row_to_response(row)


@router.patch("/connections/{id}", response_model=ConnectionResponse)
async def patch_connection(
    id: UUID,
    body: ConnectionPatch,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ConnectionResponse:
    # block patching system sources
    settings = get_settings()
    if any(str(s.id) == str(id) for s in settings.datasources):
        raise HTTPException(status_code=404, detail="connection not found")

    creds_enc = encrypt_credentials(_creds_to_dict(body.credentials)) if body.credentials else None
    row = await update_connection(pool, id=id, owner_key=auth.public_key, label=body.label, credentials_enc=creds_enc)
    if row is None:
        raise HTTPException(status_code=404, detail="connection not found")
    return _row_to_response(row)


@router.delete("/connections/{id}", status_code=204)
async def remove_connection(
    id: UUID,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> None:
    settings = get_settings()
    if any(str(s.id) == str(id) for s in settings.datasources):
        raise HTTPException(status_code=404, detail="connection not found")

    row = await get_connection(pool, id=id, owner_key=auth.public_key)
    if row is None:
        raise HTTPException(status_code=404, detail="connection not found")

    ref_count = await count_referencing_jobs(pool, connection_id=id)
    if ref_count > 0:
        raise HTTPException(status_code=409, detail=f"connection is used by {ref_count} sync job(s)")

    await delete_connection(pool, id=id, owner_key=auth.public_key)


# ── Ping ──────────────────────────────────────────────────────────────────────

@router.post("/connections/{id}/ping", response_model=PingResponse)
async def ping_connection(
    id: UUID,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    system_sources: list[SystemSourceConfig] = Depends(get_system_sources),
) -> PingResponse:
    # Check system sources first
    for src in system_sources:
        if src.id == id:
            creds = {f: getattr(src, f) for f in src.__dataclass_fields__ if f not in ("name", "type")}
            adapter = get_adapter(src, creds)
            t0 = time.perf_counter()
            try:
                await adapter.ping()
                return PingResponse(status="reachable", latency_ms=round((time.perf_counter() - t0) * 1000, 1))
            except Exception as exc:
                return PingResponse(status="unreachable", error=str(exc))

    row = await get_connection(pool, id=id, owner_key=auth.public_key)
    if row is None:
        raise HTTPException(status_code=404, detail="connection not found")

    creds = decrypt_credentials(row["credentials_enc"])
    adapter = get_adapter(row, creds)
    t0 = time.perf_counter()
    try:
        await adapter.ping()
        status = "reachable"
        error = None
    except Exception as exc:
        status = "unreachable"
        error = str(exc)

    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    await update_connection_status(pool, id=id, status=status, last_tested_at=datetime.now(timezone.utc))
    return PingResponse(status=status, latency_ms=latency_ms, error=error)


# ── Preview ───────────────────────────────────────────────────────────────────

@router.post("/connections/{id}/preview", response_model=PreviewResponse)
async def preview_connection(
    id: UUID,
    body: PreviewRequest | None = None,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    system_sources: list[SystemSourceConfig] = Depends(get_system_sources),
) -> PreviewResponse:
    body = body or PreviewRequest()

    for src in system_sources:
        if src.id == id:
            if src.type == "dataset":
                raise HTTPException(status_code=400, detail="preview is only available for source connections")
            creds = {f: getattr(src, f) for f in src.__dataclass_fields__ if f not in ("name", "type")}
            adapter = get_adapter(src, creds)
            try:
                results = await adapter.preview(body.query, body.start, body.end, body.limit)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=str(exc))
            return PreviewResponse(results=results, connection_id=id)

    row = await get_connection(pool, id=id, owner_key=auth.public_key)
    if row is None:
        raise HTTPException(status_code=404, detail="connection not found")
    if row["role"] == "sink":
        raise HTTPException(status_code=400, detail="preview is only available for source connections")

    creds = decrypt_credentials(row["credentials_enc"])
    adapter = get_adapter(row, creds)
    try:
        results = await adapter.preview(body.query, body.start, body.end, body.limit)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="preview not yet implemented for this backend")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return PreviewResponse(results=results, connection_id=id)


# ── Schema ────────────────────────────────────────────────────────────────────

@router.get("/connections/{id}/schema", response_model=SchemaResponse)
async def schema_connection(
    id: UUID,
    start: datetime | None = None,
    end: datetime | None = None,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> SchemaResponse:
    row = await get_connection(pool, id=id, owner_key=auth.public_key)
    if row is None:
        raise HTTPException(status_code=404, detail="connection not found")
    if row["role"] == "sink":
        raise HTTPException(status_code=400, detail="schema discovery is only available for source connections")

    creds = decrypt_credentials(row["credentials_enc"])
    adapter = get_adapter(row, creds)
    try:
        fields_raw, sample_count = await adapter.schema(start, end)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="schema discovery not yet implemented for this backend")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    from databridge.models import SchemaField
    fields = {k: SchemaField(**v) if isinstance(v, dict) else v for k, v in fields_raw.items()}
    return SchemaResponse(fields=fields, sample_count=sample_count, connection_id=id)
