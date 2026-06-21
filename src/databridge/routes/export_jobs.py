from __future__ import annotations

from pathlib import Path
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from databridge.auth import AuthContext, get_auth
from databridge.config import get_settings
from databridge.db.pool import get_pool
from databridge.export.db import (
    cancel_export_job,
    count_active_jobs_for_org,
    get_export_job,
    insert_export_job,
    list_export_jobs,
)
from databridge.export.models import (
    ExportJobCreate,
    ExportJobListResponse,
    ExportJobResponse,
    ExportJobStatus,
)
from databridge.export_metrics import EXPORT_JOBS_CREATED

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["export-jobs"])

_LOCAL_SINK_TYPES = {"local-zip", "local-jsonl"}
_DATASET_MOCK_TYPE = "dataset-mock"
_ANNOTATOR_MOCK_TYPE = "annotator-mock"


def _with_download_urls(job: ExportJobResponse) -> ExportJobResponse:
    settings = get_settings()
    base = settings.server.public_url.rstrip("/")
    updates: dict = {}

    datasink_cfg = next((s for s in settings.datasinks if s.name == job.datasink_name), None)
    if datasink_cfg and datasink_cfg.type in _LOCAL_SINK_TYPES:
        path = f"/api/v1/export-jobs/{job.id}/download"
        updates["download_url"] = f"{base}{path}" if base else path
        if job.asset_resolution and job.asset_datasink_name:
            asset_cfg = next((s for s in settings.datasinks if s.name == job.asset_datasink_name), None)
            if asset_cfg and asset_cfg.type in _LOCAL_SINK_TYPES:
                assets_path = f"/api/v1/export-jobs/{job.id}/download?assets=true"
                updates["assets_download_url"] = f"{base}{assets_path}" if base else assets_path
    elif datasink_cfg and datasink_cfg.type == _DATASET_MOCK_TYPE:
        sink_base = datasink_cfg.url.rstrip("/")
        if job.external_dataset_id:
            updates["download_url"] = f"{sink_base}/_mock/datasets/{job.external_dataset_id}"
        else:
            updates["download_url"] = f"{sink_base}/_mock/datasets"
        if job.asset_resolution and job.external_asset_dataset_id:
            asset_cfg = next((s for s in settings.datasinks if s.name == job.asset_datasink_name), None)
            asset_base = asset_cfg.url.rstrip("/") if asset_cfg else sink_base
            updates["assets_download_url"] = f"{asset_base}/_mock/datasets/{job.external_asset_dataset_id}"
    elif datasink_cfg and datasink_cfg.type == _ANNOTATOR_MOCK_TYPE:
        sink_base = datasink_cfg.url.rstrip("/")
        if job.external_dataset_id:
            updates["download_url"] = f"{sink_base}/api/v0/statistics/task/{job.external_dataset_id}"
        else:
            updates["download_url"] = f"{sink_base}/api/v0/tasks"
    elif datasink_cfg:
        updates["download_url"] = datasink_cfg.url or ""

    return job.model_copy(update=updates) if updates else job


@router.post("/api/v1/export-jobs", response_model=ExportJobResponse, status_code=201)
async def create_export_job(
    body: ExportJobCreate,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ExportJobResponse:
    settings = get_settings()

    # Validate datasink exists
    datasink_cfg = next((s for s in settings.datasinks if s.name == body.datasink_name), None)
    if datasink_cfg is None:
        raise HTTPException(status_code=400, detail=f"datasink '{body.datasink_name}' not configured")

    # Per-org concurrency check
    active = await count_active_jobs_for_org(pool, auth.org_id)
    if active >= settings.export.max_concurrent_jobs_per_org:
        raise HTTPException(
            status_code=429,
            detail=(
                f"concurrent job limit reached: {active}/{settings.export.max_concurrent_jobs_per_org} "
                f"active jobs for org '{auth.org_id}'"
            ),
        )

    arq_pool = _get_arq_pool()
    if arq_pool is None:
        raise HTTPException(status_code=503, detail="export worker queue unavailable")
    try:
        await arq_pool.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"export worker queue unreachable: {exc}") from exc

    job = await insert_export_job(pool, body, org_id=auth.org_id, user_id=auth.user_id)

    try:
        await arq_pool.enqueue_job("run_export_job", str(job.id), _job_id=str(job.id))
    except Exception as exc:
        logger.error("arq_enqueue_failed", job_id=str(job.id), error=str(exc))
        raise HTTPException(status_code=503, detail=f"failed to enqueue job: {exc}") from exc

    EXPORT_JOBS_CREATED.labels(org_id=auth.org_id, sink_type=datasink_cfg.type).inc()
    return _with_download_urls(job)


@router.get("/api/v1/export-jobs", response_model=ExportJobListResponse)
async def list_export_jobs_endpoint(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ExportJobListResponse:
    jobs, total = await list_export_jobs(
        pool,
        org_id=auth.org_id,
        user_id=auth.user_id,
        role=auth.role,
        page=page,
        page_size=page_size,
        status_filter=status,
    )
    return ExportJobListResponse(
        items=[_with_download_urls(j) for j in jobs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/api/v1/export-jobs/{job_id}", response_model=ExportJobResponse)
async def get_export_job_endpoint(
    job_id: UUID,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ExportJobResponse:
    job = await get_export_job(pool, job_id, auth.org_id, auth.user_id, auth.role)
    if job is None:
        raise HTTPException(status_code=404, detail="export job not found")
    return _with_download_urls(job)


@router.post("/api/v1/export-jobs/{job_id}/retry", response_model=ExportJobResponse, status_code=201)
async def retry_export_job(
    job_id: UUID,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ExportJobResponse:
    settings = get_settings()

    original = await get_export_job(pool, job_id, auth.org_id, auth.user_id, auth.role)
    if original is None:
        raise HTTPException(status_code=404, detail="export job not found")
    if original.status != ExportJobStatus.failed:
        raise HTTPException(status_code=400, detail="only failed jobs can be retried")

    active = await count_active_jobs_for_org(pool, auth.org_id)
    if active >= settings.export.max_concurrent_jobs_per_org:
        raise HTTPException(
            status_code=429,
            detail=f"concurrent job limit reached for org '{auth.org_id}'",
        )

    new_job_data = ExportJobCreate(
        datasource_type=original.datasource_type,  # type: ignore[arg-type]
        datasource_ref=original.datasource_ref,
        datasource_filter=original.datasource_filter,
        datasink_name=original.datasink_name,
        destination_dataset=original.destination_dataset,
        asset_dataset=original.asset_dataset,
        asset_resolution=original.asset_resolution,
        asset_url_fields=original.asset_url_fields,
        asset_url_prefix=original.asset_url_prefix,
        asset_datasink_name=original.asset_datasink_name,
        masking_rules=original.masking_rules,
        sampling_config=original.sampling_config,
        webhook_url=original.webhook_url,
        webhook_enabled=original.webhook_enabled,
        webhook_payload_template=original.webhook_payload_template,
    )
    arq_pool = _get_arq_pool()
    if arq_pool is None:
        raise HTTPException(status_code=503, detail="export worker queue unavailable")
    try:
        await arq_pool.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"export worker queue unreachable: {exc}") from exc

    new_job = await insert_export_job(pool, new_job_data, org_id=auth.org_id, user_id=auth.user_id)

    try:
        await arq_pool.enqueue_job("run_export_job", str(new_job.id), _job_id=str(new_job.id))
    except Exception as exc:
        logger.error("arq_enqueue_failed", job_id=str(new_job.id), error=str(exc))
        raise HTTPException(status_code=503, detail=f"failed to enqueue job: {exc}") from exc

    datasink_cfg = next((s for s in settings.datasinks if s.name == new_job.datasink_name), None)
    EXPORT_JOBS_CREATED.labels(
        org_id=auth.org_id, sink_type=datasink_cfg.type if datasink_cfg else "unknown"
    ).inc()
    return _with_download_urls(new_job)


@router.post("/api/v1/export-jobs/{job_id}/cancel", status_code=204)
async def cancel_export_job_endpoint(
    job_id: UUID,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> None:
    ok = await cancel_export_job(pool, job_id, auth.org_id, auth.user_id, auth.role)
    if not ok:
        job = await get_export_job(pool, job_id, auth.org_id, auth.user_id, auth.role)
        if job is None:
            raise HTTPException(status_code=404, detail="export job not found")
        raise HTTPException(
            status_code=409,
            detail=f"job cannot be cancelled (current status: {job.status.value})",
        )


_EXTENSIONS = {"local-zip": ".zip", "local-jsonl": ".jsonl"}
_MEDIA_TYPES = {"local-zip": "application/zip", "local-jsonl": "application/x-ndjson"}


def _local_file_response(datasink_cfg, dataset: str, job_id: UUID) -> FileResponse:
    ext = _EXTENSIONS[datasink_cfg.type]
    base = Path(datasink_cfg.path)
    candidates = [
        base / f"{dataset}_{job_id}{ext}",
        base / f"{dataset}{ext}",
    ]
    filepath = next((p for p in candidates if p.exists()), None)
    if filepath is None:
        raise HTTPException(
            status_code=404,
            detail=f"output file not found in {datasink_cfg.path} for dataset '{dataset}'",
        )
    return FileResponse(path=str(filepath), filename=filepath.name, media_type=_MEDIA_TYPES[datasink_cfg.type])


@router.get("/api/v1/export-jobs/{job_id}/download")
async def download_export(
    job_id: UUID,
    assets: bool = Query(default=False),
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> FileResponse:
    settings = get_settings()
    job = await get_export_job(pool, job_id, auth.org_id, auth.user_id, auth.role)
    if job is None:
        raise HTTPException(status_code=404, detail="export job not found")
    if job.status != ExportJobStatus.completed:
        raise HTTPException(status_code=409, detail=f"job is {job.status.value}, not completed")

    if assets:
        if not job.asset_resolution or not job.asset_datasink_name or not job.asset_dataset:
            raise HTTPException(status_code=400, detail="job has no asset resolution configured")
        asset_cfg = next((s for s in settings.datasinks if s.name == job.asset_datasink_name), None)
        if asset_cfg is None or asset_cfg.type not in _LOCAL_SINK_TYPES:
            raise HTTPException(status_code=400, detail="asset download only available for local-zip and local-jsonl sinks")
        return _local_file_response(asset_cfg, job.asset_dataset, job_id)

    datasink_cfg = next((s for s in settings.datasinks if s.name == job.datasink_name), None)
    if datasink_cfg is None or datasink_cfg.type not in _LOCAL_SINK_TYPES:
        raise HTTPException(status_code=400, detail="download only available for local-zip and local-jsonl sinks")
    return _local_file_response(datasink_cfg, job.destination_dataset, job_id)


class _WebhookTestRequest(BaseModel):
    url: str
    template: str | None = None


def _validate_webhook_url(url: str, allowed_prefixes: tuple[str, ...]) -> None:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="webhook URL must use http or https scheme")
    if allowed_prefixes and not any(url.startswith(p) for p in allowed_prefixes):
        raise HTTPException(
            status_code=400,
            detail="webhook URL is not in the allowed prefixes configured by your administrator",
        )


@router.post("/api/v1/export-jobs/test-webhook", status_code=204)
async def test_webhook_endpoint(
    body: _WebhookTestRequest,
    auth: AuthContext = Depends(get_auth),
) -> None:
    import httpx
    from databridge.export.webhook import render_payload

    settings = get_settings()
    _validate_webhook_url(body.url, settings.export.webhook_allowed_url_prefixes)

    _ctx = {
        "job_id": "job_id", "status": "status", "org_id": "org_id",
        "destination_dataset": "destination_dataset", "records_processed": "records_processed",
        "records_skipped": "records_skipped", "error": "error", "download_url": "download_url",
    }
    payload = render_payload(body.template, _ctx) if body.template else {"status": "test", "message": "Webhook test from DataBridge"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(body.url, json=payload)
            r.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Webhook delivery failed: {exc}")


# Module-level reference to ARQ pool, set by lifespan
_arq_pool_ref = None


def _get_arq_pool():
    return _arq_pool_ref


def set_arq_pool(pool) -> None:
    global _arq_pool_ref
    _arq_pool_ref = pool
