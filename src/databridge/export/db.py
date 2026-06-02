from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from databridge.export.models import ExportJob, ExportJobCreate, ExportJobResponse, ExportJobStatus, FilterSnapshot


def _row_to_response(row: asyncpg.Record) -> ExportJobResponse:
    filter_raw = row["datasource_filter"]
    if isinstance(filter_raw, str):
        filter_raw = json.loads(filter_raw)
    filter_snap = FilterSnapshot(**(filter_raw or {}))

    url_fields_raw = row["asset_url_fields"]
    if isinstance(url_fields_raw, str):
        url_fields_raw = json.loads(url_fields_raw)

    return ExportJobResponse(
        id=row["id"],
        org_id=row["org_id"],
        user_id=row["user_id"],
        datasource_type=row["datasource_type"],
        datasource_ref=row["datasource_ref"],
        datasource_filter=filter_snap,
        datasink_name=row["datasink_name"],
        destination_dataset=row["destination_dataset"],
        asset_resolution=row["asset_resolution"],
        asset_url_fields=url_fields_raw or [],
        asset_url_prefix=row["asset_url_prefix"] or "",
        asset_datasink_name=row["asset_datasink_name"],
        asset_dataset=row["asset_dataset"],
        status=ExportJobStatus(row["status"]),
        records_total=row["records_total"],
        records_processed=row["records_processed"],
        records_skipped=row["records_skipped"],
        asset_errors=row["asset_errors"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


async def insert_export_job(
    pool: asyncpg.Pool,
    data: ExportJobCreate,
    org_id: str,
    user_id: str,
) -> ExportJobResponse:
    asset_dataset = (
        f"{data.destination_dataset}_assets"
        if data.asset_resolution and data.asset_datasink_name
        else None
    )
    row = await pool.fetchrow(
        """
        INSERT INTO export_jobs (
            org_id, user_id, datasource_type, datasource_ref, datasource_filter,
            datasink_name, destination_dataset,
            asset_resolution, asset_url_fields, asset_url_prefix,
            asset_datasink_name, asset_dataset
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        RETURNING *
        """,
        org_id,
        user_id,
        data.datasource_type,
        data.datasource_ref,
        json.dumps(data.datasource_filter.model_dump(mode="json")),
        data.datasink_name,
        data.destination_dataset,
        data.asset_resolution,
        json.dumps(data.asset_url_fields),
        data.asset_url_prefix,
        data.asset_datasink_name,
        asset_dataset,
    )
    return _row_to_response(row)


async def get_export_job(
    pool: asyncpg.Pool,
    job_id: UUID,
    org_id: str,
    user_id: str,
    role: str,
) -> ExportJobResponse | None:
    row = await pool.fetchrow("SELECT * FROM export_jobs WHERE id = $1", job_id)
    if row is None:
        return None
    if role == "super_admin":
        return _row_to_response(row)
    if role == "org_admin" and row["org_id"] == org_id:
        return _row_to_response(row)
    if row["org_id"] == org_id and row["user_id"] == user_id:
        return _row_to_response(row)
    return None


async def list_export_jobs(
    pool: asyncpg.Pool,
    org_id: str,
    user_id: str,
    role: str,
    page: int = 1,
    page_size: int = 20,
    status_filter: str | None = None,
) -> tuple[list[ExportJobResponse], int]:
    conditions: list[str] = []
    params: list = []

    if role == "super_admin":
        pass
    elif role == "org_admin":
        params.append(org_id)
        conditions.append(f"org_id = ${len(params)}")
    else:
        params.append(org_id)
        conditions.append(f"org_id = ${len(params)}")
        params.append(user_id)
        conditions.append(f"user_id = ${len(params)}")

    if status_filter:
        params.append(status_filter)
        conditions.append(f"status = ${len(params)}")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total = await pool.fetchval(f"SELECT COUNT(*) FROM export_jobs {where}", *params)

    offset = (page - 1) * page_size
    params.extend([page_size, offset])
    rows = await pool.fetch(
        f"SELECT * FROM export_jobs {where} ORDER BY created_at DESC LIMIT ${len(params) - 1} OFFSET ${len(params)}",
        *params,
    )
    return [_row_to_response(r) for r in rows], total


async def update_export_job_status(
    pool: asyncpg.Pool,
    job_id: UUID,
    status: ExportJobStatus,
    error_message: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    if status == ExportJobStatus.running:
        await pool.execute(
            "UPDATE export_jobs SET status=$1, started_at=$2, last_heartbeat_at=$2 WHERE id=$3",
            status.value, now, job_id,
        )
    elif status in (ExportJobStatus.completed, ExportJobStatus.failed):
        await pool.execute(
            "UPDATE export_jobs SET status=$1, completed_at=$2, error_message=$3 WHERE id=$4",
            status.value, now, error_message, job_id,
        )
    else:
        await pool.execute("UPDATE export_jobs SET status=$1 WHERE id=$2", status.value, job_id)


async def update_export_progress(
    pool: asyncpg.Pool,
    job_id: UUID,
    records_processed: int,
    records_skipped: int,
    asset_errors: int,
) -> None:
    now = datetime.now(timezone.utc)
    await pool.execute(
        """
        UPDATE export_jobs
        SET records_processed=$1, records_skipped=$2, asset_errors=$3, last_heartbeat_at=$4
        WHERE id=$5
        """,
        records_processed, records_skipped, asset_errors, now, job_id,
    )


async def update_records_total(pool: asyncpg.Pool, job_id: UUID, records_total: int) -> None:
    await pool.execute(
        "UPDATE export_jobs SET records_total=$1 WHERE id=$2",
        records_total, job_id,
    )


async def count_active_jobs_for_org(pool: asyncpg.Pool, org_id: str) -> int:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", org_id)
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM export_jobs WHERE org_id=$1 AND status IN ('pending','running')",
                org_id,
            )
    return count
