from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException

import httpx

from databridge.auth import AuthContext, get_auth
from databridge.config import get_settings, DatasinkConfig
from databridge.export.models import (
    AssetFieldDetectRequest,
    AssetFieldDetectResponse,
    DatasinkDatasetListResponse,
    DatasinkInfo,
    DatasinkListResponse,
)
from databridge.sinks import get_sink

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["datasinks"])


@router.get("/api/v1/datasinks", response_model=DatasinkListResponse)
async def list_datasinks(auth: AuthContext = Depends(get_auth)) -> DatasinkListResponse:
    settings = get_settings()
    return DatasinkListResponse(
        datasinks=[DatasinkInfo(name=s.name, type=s.type) for s in settings.datasinks]
    )


@router.get("/api/v1/datasinks/{name}/datasets", response_model=DatasinkDatasetListResponse)
async def get_datasink_datasets(
    name: str,
    auth: AuthContext = Depends(get_auth),
) -> DatasinkDatasetListResponse:
    settings = get_settings()
    cfg: DatasinkConfig | None = next((s for s in settings.datasinks if s.name == name), None)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"datasink '{name}' not found")
    sink = get_sink(cfg)
    try:
        datasets = await sink.list_datasets()
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
        logger.warning("datasink_unreachable", name=name, error=str(exc))
        raise HTTPException(status_code=502, detail=f"datasink '{name}' is unreachable")
    return DatasinkDatasetListResponse(datasets=datasets)


@router.post("/api/v1/datasinks/{name}/detect-asset-fields", response_model=AssetFieldDetectResponse)
async def detect_asset_fields(
    name: str,
    body: AssetFieldDetectRequest,
    auth: AuthContext = Depends(get_auth),
) -> AssetFieldDetectResponse:
    from databridge.db.pool import get_pool
    from fastapi import Request

    if body.connection_id is None and body.system_source_name is None:
        raise HTTPException(status_code=400, detail="provide connection_id or system_source_name")
    if body.connection_id is not None and body.system_source_name is not None:
        raise HTTPException(status_code=400, detail="provide only one of connection_id or system_source_name")

    settings = get_settings()

    if body.system_source_name:
        cfg = next((s for s in settings.datasources if s.name == body.system_source_name), None)
        if cfg is None:
            raise HTTPException(status_code=404, detail=f"system source '{body.system_source_name}' not found")
        from databridge.adapters import get_adapter
        adapter = get_adapter(cfg, {})
    else:
        # connection_id path — requires DB pool
        raise HTTPException(status_code=501, detail="connection_id support requires DB context; use system_source_name")

    schema_fields, _ = await adapter.schema(None, None)
    sample_records = await adapter.preview("", None, None, limit=20)

    from databridge.export.asset import detect_asset_url_fields
    candidates = detect_asset_url_fields(schema_fields, sample_records)
    return AssetFieldDetectResponse(candidate_fields=candidates)
