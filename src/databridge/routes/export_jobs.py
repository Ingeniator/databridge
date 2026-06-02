from __future__ import annotations

from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from databridge.auth import AuthContext, get_auth
from databridge.config import get_settings
from databridge.db.pool import get_pool
from databridge.export.db import (
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

    job = await insert_export_job(pool, body, org_id=auth.org_id, user_id=auth.user_id)

    # Enqueue ARQ job
    try:
        from fastapi import Request
        arq_pool = _get_arq_pool()
        if arq_pool is not None:
            await arq_pool.enqueue_job("run_export_job", str(job.id), _job_id=str(job.id))
    except Exception as exc:
        logger.warning("arq_enqueue_failed", job_id=str(job.id), error=str(exc))

    EXPORT_JOBS_CREATED.labels(org_id=auth.org_id, sink_type=datasink_cfg.type).inc()
    return job


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
    return ExportJobListResponse(items=jobs, total=total, page=page, page_size=page_size)


@router.get("/api/v1/export-jobs/{job_id}", response_model=ExportJobResponse)
async def get_export_job_endpoint(
    job_id: UUID,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ExportJobResponse:
    job = await get_export_job(pool, job_id, auth.org_id, auth.user_id, auth.role)
    if job is None:
        raise HTTPException(status_code=404, detail="export job not found")
    return job


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
        asset_resolution=original.asset_resolution,
        asset_url_fields=original.asset_url_fields,
        asset_url_prefix=original.asset_url_prefix,
        asset_datasink_name=original.asset_datasink_name,
    )
    new_job = await insert_export_job(pool, new_job_data, org_id=auth.org_id, user_id=auth.user_id)

    try:
        arq_pool = _get_arq_pool()
        if arq_pool is not None:
            await arq_pool.enqueue_job("run_export_job", str(new_job.id), _job_id=str(new_job.id))
    except Exception as exc:
        logger.warning("arq_enqueue_failed", job_id=str(new_job.id), error=str(exc))

    datasink_cfg = next((s for s in settings.datasinks if s.name == new_job.datasink_name), None)
    EXPORT_JOBS_CREATED.labels(
        org_id=auth.org_id, sink_type=datasink_cfg.type if datasink_cfg else "unknown"
    ).inc()
    return new_job


# Module-level reference to ARQ pool, set by lifespan
_arq_pool_ref = None


def _get_arq_pool():
    return _arq_pool_ref


def set_arq_pool(pool) -> None:
    global _arq_pool_ref
    _arq_pool_ref = pool
