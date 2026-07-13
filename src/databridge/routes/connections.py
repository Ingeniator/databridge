from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from databridge.adapters import apply_time_field_override, get_adapter, _infer_schema
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
from databridge.export.models import (
    AssetResolutionTestRequest,
    AssetResolutionTestResponse,
    AssetUrlTestResult,
    FieldExtractionTestRequest,
    FieldExtractionTestResponse,
    FieldExtractionTestResult,
    PiiFieldsResponse,
)
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
    except Exception as exc:
        return PingResponse(status="unreachable", error=str(exc))

    latency_ms = round((time.perf_counter() - t0) * 1000, 1)

    # Verify credentials by fetching the schema (ping alone doesn't require auth)
    try:
        await adapter.schema(None, None)
        return PingResponse(status="reachable", latency_ms=latency_ms, auth_ok=True)
    except Exception as exc:
        return PingResponse(status="reachable", latency_ms=latency_ms, auth_ok=False, auth_error=str(exc))


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


_SECRET_KEYS = re.compile(r"password|secret|token|key", re.IGNORECASE)


@router.get("/connections/{id}/credentials")
async def get_connection_credentials(
    row: asyncpg.Record = Depends(get_connection_or_404),
) -> dict:
    creds = decrypt_credentials(bytes(row["credentials_enc"]))
    return {k: v for k, v in creds.items() if not _SECRET_KEYS.search(k)}


@router.patch("/connections/{id}", response_model=ConnectionResponse)
async def patch_connection(
    id: UUID,
    body: ConnectionPatch,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ConnectionResponse:
    settings = get_settings()
    if any(str(s.id) == str(id) for s in settings.datasources):
        raise HTTPException(status_code=404, detail="connection not found")

    creds_enc: bytes | None = None
    if body.credentials:
        row = await get_connection(pool, id=id, owner_key=auth.public_key)
        if row is None:
            raise HTTPException(status_code=404, detail="connection not found")
        existing = decrypt_credentials(bytes(row["credentials_enc"]))
        incoming = _creds_to_dict(body.credentials)
        _CLEARABLE = {"timestamp_column"}
        merged = {**existing}
        for k, v in incoming.items():
            if v is None:
                continue
            if v == "" and k not in _CLEARABLE:
                continue
            merged[k] = v
        creds_enc = encrypt_credentials(merged)

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
            adapter, creds = apply_time_field_override(adapter, src, creds, body.time_field)
            try:
                results, total_count = await asyncio.gather(
                    adapter.preview(body.query, body.start, body.end, body.limit, sort_by=body.sort_by),
                    _safe_count(adapter, body.query, body.start, body.end),
                )
            except Exception as exc:
                raise HTTPException(status_code=502, detail=str(exc))
            schema_fields = _infer_schema(results[:20])
            return PreviewResponse(results=results, connection_id=id, total_count=total_count, schema_fields=schema_fields)

    row = await get_connection(pool, id=id, owner_key=auth.public_key)
    if row is None:
        raise HTTPException(status_code=404, detail="connection not found")
    if row["role"] == "sink":
        raise HTTPException(status_code=400, detail="preview is only available for source connections")

    creds = decrypt_credentials(row["credentials_enc"])
    adapter = get_adapter(row, creds)
    adapter, creds = apply_time_field_override(adapter, row, creds, body.time_field)
    try:
        results, total_count = await asyncio.gather(
            adapter.preview(body.query, body.start, body.end, body.limit, sort_by=body.sort_by),
            _safe_count(adapter, body.query, body.start, body.end),
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="preview not yet implemented for this backend")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    schema_fields = _infer_schema(results[:20])
    return PreviewResponse(results=results, connection_id=id, total_count=total_count, schema_fields=schema_fields)


async def _safe_count(adapter, query: str, start, end) -> int:
    try:
        return await adapter.count(query, start, end)
    except (NotImplementedError, Exception):
        return 0


# ── Schema ────────────────────────────────────────────────────────────────────

@router.get("/connections/{id}/schema", response_model=SchemaResponse)
async def schema_connection(
    id: UUID,
    start: datetime | None = None,
    end: datetime | None = None,
    time_field: str | None = None,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    system_sources: list[SystemSourceConfig] = Depends(get_system_sources),
) -> SchemaResponse:
    for src in system_sources:
        if src.id == id:
            creds = {f: getattr(src, f) for f in src.__dataclass_fields__ if f not in ("name", "type")}
            adapter = get_adapter(src, creds)
            adapter, creds = apply_time_field_override(adapter, src, creds, time_field)
            try:
                fields_raw, sample_count = await adapter.schema(start, end)
            except NotImplementedError:
                raise HTTPException(status_code=501, detail="schema discovery not yet implemented for this backend")
            except Exception as exc:
                raise HTTPException(status_code=502, detail=str(exc))
            from databridge.models import SchemaField
            fields = {k: SchemaField(**v) if isinstance(v, dict) else v for k, v in fields_raw.items()}
            return SchemaResponse(fields=fields, sample_count=sample_count, connection_id=id)

    row = await get_connection(pool, id=id, owner_key=auth.public_key)
    if row is None:
        raise HTTPException(status_code=404, detail="connection not found")
    if row["role"] == "sink":
        raise HTTPException(status_code=400, detail="schema discovery is only available for source connections")

    creds = decrypt_credentials(row["credentials_enc"])
    adapter = get_adapter(row, creds)
    adapter, creds = apply_time_field_override(adapter, row, creds, time_field)
    try:
        fields_raw, sample_count = await adapter.schema(start, end)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="schema discovery not yet implemented for this backend")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    from databridge.models import SchemaField
    fields = {k: SchemaField(**v) if isinstance(v, dict) else v for k, v in fields_raw.items()}
    return SchemaResponse(fields=fields, sample_count=sample_count, connection_id=id)


# ── PII field detection ───────────────────────────────────────────────────────

_PII_PATTERNS = ("email", "phone", "ssn", "password", "ip", "user_id", "token", "secret", "card")


@router.get("/connections/{id}/pii-fields", response_model=PiiFieldsResponse)
async def pii_fields(
    id: UUID,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    system_sources: list[SystemSourceConfig] = Depends(get_system_sources),
) -> PiiFieldsResponse:
    from databridge.export.masking import pii_candidate_fields

    for src in system_sources:
        if src.id == id:
            creds = {f: getattr(src, f) for f in src.__dataclass_fields__ if f not in ("name", "type")}
            adapter = get_adapter(src, creds)
            try:
                fields_raw, _ = await adapter.schema(None, None, nested=True)
            except Exception:
                fields_raw = {}
            return PiiFieldsResponse(candidate_fields=pii_candidate_fields(fields_raw))

    row = await get_connection(pool, id=id, owner_key=auth.public_key)
    if row is None:
        raise HTTPException(status_code=404, detail="connection not found")

    creds = decrypt_credentials(row["credentials_enc"])
    adapter = get_adapter(row, creds)
    try:
        fields_raw, _ = await adapter.schema(None, None, nested=True)
    except Exception:
        fields_raw = {}
    return PiiFieldsResponse(candidate_fields=pii_candidate_fields(fields_raw))


# ── Asset resolution test ─────────────────────────────────────────────────────

@router.post("/connections/{id}/test-asset-resolution", response_model=AssetResolutionTestResponse)
async def test_asset_resolution(
    id: UUID,
    body: AssetResolutionTestRequest,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    system_sources: list[SystemSourceConfig] = Depends(get_system_sources),
) -> AssetResolutionTestResponse:
    import re
    _URL_RE = re.compile(r"^https?://", re.IGNORECASE)

    # Resolve adapter (system source or DB connection)
    adapter = None
    for src in system_sources:
        if src.id == id:
            creds = {f: getattr(src, f) for f in src.__dataclass_fields__ if f not in ("name", "type")}
            adapter = get_adapter(src, creds)
            break
    if adapter is None:
        row = await get_connection(pool, id=id, owner_key=auth.public_key)
        if row is None:
            raise HTTPException(status_code=404, detail="connection not found")
        creds = decrypt_credentials(row["credentials_enc"])
        adapter = get_adapter(row, creds)

    try:
        records = await adapter.preview("", None, None, limit=5)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"preview failed: {exc}")

    # Collect up to 2 sample URLs per configured field
    samples: list[tuple[str, str]] = []
    for field in body.url_fields:
        count = 0
        for rec in records:
            val = rec.get(field)
            if val and isinstance(val, str) and _URL_RE.match(val):
                samples.append((field, val))
                count += 1
                if count >= 2:
                    break

    if not samples:
        return AssetResolutionTestResponse(results=[])

    import httpx
    results: list[AssetUrlTestResult] = []
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for field, raw in samples:
            url = (body.url_prefix + raw) if body.url_prefix else raw
            try:
                r = await client.head(url)
                results.append(AssetUrlTestResult(
                    field=field,
                    raw_value=raw,
                    resolved_url=url,
                    status_code=r.status_code,
                    ok=r.status_code < 400,
                ))
            except httpx.RequestError as exc:
                results.append(AssetUrlTestResult(
                    field=field,
                    raw_value=raw,
                    resolved_url=url,
                    ok=False,
                    error=str(exc),
                ))

    return AssetResolutionTestResponse(results=results)


# ── Field extraction test ──────────────────────────────────────────────────────

@router.post("/connections/{id}/test-field-extraction", response_model=FieldExtractionTestResponse)
async def test_field_extraction(
    id: UUID,
    body: FieldExtractionTestRequest,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    system_sources: list[SystemSourceConfig] = Depends(get_system_sources),
) -> FieldExtractionTestResponse:
    from databridge.export.extraction import extract_field_value

    # Resolve adapter (system source or DB connection)
    adapter = None
    for src in system_sources:
        if src.id == id:
            creds = {f: getattr(src, f) for f in src.__dataclass_fields__ if f not in ("name", "type")}
            adapter = get_adapter(src, creds)
            break
    if adapter is None:
        row = await get_connection(pool, id=id, owner_key=auth.public_key)
        if row is None:
            raise HTTPException(status_code=404, detail="connection not found")
        creds = decrypt_credentials(row["credentials_enc"])
        adapter = get_adapter(row, creds)

    try:
        records = await adapter.preview("", None, None, limit=5)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"preview failed: {exc}")

    results: list[FieldExtractionTestResult] = []
    for rec in records:
        value = extract_field_value(rec, body.field_path)
        if value is None:
            results.append(FieldExtractionTestResult(
                resolved=False,
                error="field not found or not JSON-parseable content",
            ))
        else:
            results.append(FieldExtractionTestResult(
                resolved=True,
                value_preview=json.dumps(value)[:500],
            ))

    return FieldExtractionTestResponse(results=results)
