from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg

from databridge.export.models import (
    ExportJob,
    ExportJobCreate,
    ExportJobResponse,
    ExportJobStatus,
    FilterSnapshot,
    MaskingRule,
    SamplingConfig,
)


def _parse_masking_rules(raw) -> list[MaskingRule]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = json.loads(raw)
    return [MaskingRule(**r) for r in (raw or [])]


def _parse_sampling_config(raw) -> SamplingConfig | None:
    if not raw:
        return None
    if isinstance(raw, str):
        raw = json.loads(raw)
    return SamplingConfig(**raw) if raw else None


def _row_to_response(row: asyncpg.Record) -> ExportJobResponse:
    filter_raw = row["datasource_filter"]
    if isinstance(filter_raw, str):
        filter_raw = json.loads(filter_raw)
    filter_snap = FilterSnapshot(**(filter_raw or {}))

    url_fields_raw = row["asset_url_fields"]
    if isinstance(url_fields_raw, str):
        url_fields_raw = json.loads(url_fields_raw)

    masking_rules = _parse_masking_rules(row.get("masking_rules"))
    sampling_config = _parse_sampling_config(row.get("sampling_config"))

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
        masking_rules=masking_rules,
        sampling_config=sampling_config,
        webhook_url=row.get("webhook_url"),
        webhook_enabled=row.get("webhook_enabled", False),
        webhook_payload_template=row.get("webhook_payload_template"),
        field_extraction=row.get("field_extraction", False),
        field_extraction_path=row.get("field_extraction_path") or "",
        external_dataset_id=row.get("external_dataset_id"),
        external_asset_dataset_id=row.get("external_asset_dataset_id"),
    )


async def insert_export_job(
    pool: asyncpg.Pool,
    data: ExportJobCreate,
    org_id: str,
    user_id: str,
) -> ExportJobResponse:
    # Use explicitly provided asset_dataset, fall back to template, or None if resolution off
    if data.asset_resolution:
        asset_dataset = data.asset_dataset or f"{data.destination_dataset}_assets"
        asset_datasink_name = data.asset_datasink_name or data.datasink_name
    else:
        asset_dataset = None
        asset_datasink_name = None
    masking_rules_json = json.dumps([r.model_dump(mode="json") for r in data.masking_rules])
    sampling_config_json = json.dumps(data.sampling_config.model_dump(mode="json")) if data.sampling_config else None
    row = await pool.fetchrow(
        """
        INSERT INTO export_jobs (
            org_id, user_id, datasource_type, datasource_ref, datasource_filter,
            datasink_name, destination_dataset,
            asset_resolution, asset_url_fields, asset_url_prefix,
            asset_datasink_name, asset_dataset,
            masking_rules, sampling_config, webhook_url, webhook_enabled, webhook_payload_template,
            field_extraction, field_extraction_path
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
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
        asset_datasink_name,
        asset_dataset,
        masking_rules_json,
        sampling_config_json,
        data.webhook_url,
        data.webhook_enabled,
        data.webhook_payload_template,
        data.field_extraction,
        data.field_extraction_path,
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
    else:
        # All roles see all jobs within their org so they can understand quota usage
        params.append(org_id)
        conditions.append(f"org_id = ${len(params)}")

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


async def update_external_dataset_id(pool: asyncpg.Pool, job_id: UUID, external_dataset_id: str) -> None:
    await pool.execute(
        "UPDATE export_jobs SET external_dataset_id=$1 WHERE id=$2",
        external_dataset_id, job_id,
    )


async def update_external_asset_dataset_id(pool: asyncpg.Pool, job_id: UUID, external_asset_dataset_id: str) -> None:
    await pool.execute(
        "UPDATE export_jobs SET external_asset_dataset_id=$1 WHERE id=$2",
        external_asset_dataset_id, job_id,
    )


async def cancel_export_job(
    pool: asyncpg.Pool,
    job_id: UUID,
    org_id: str,
    user_id: str,
    role: str,
) -> bool:
    """Set status to cancelled if the job is pending or running and owned by the caller.
    Returns True if the row was updated, False if not found / wrong state / no permission."""
    row = await pool.fetchrow("SELECT org_id, user_id, status FROM export_jobs WHERE id = $1", job_id)
    if row is None:
        return False
    if role not in ("super_admin", "org_admin"):
        if row["org_id"] != org_id or row["user_id"] != user_id:
            return False
    elif role == "org_admin" and row["org_id"] != org_id:
        return False
    if row["status"] not in ("pending", "running"):
        return False
    result = await pool.execute(
        "UPDATE export_jobs SET status='cancelled' WHERE id=$1 AND status IN ('pending','running')",
        job_id,
    )
    return result == "UPDATE 1"


async def is_job_cancelled(pool: asyncpg.Pool, job_id: UUID) -> bool:
    status = await pool.fetchval("SELECT status FROM export_jobs WHERE id = $1", job_id)
    return status == "cancelled"


_STALE_JOB_TIMEOUT_MINUTES = 30


async def count_active_jobs_for_org(pool: asyncpg.Pool, org_id: str) -> int:
    stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STALE_JOB_TIMEOUT_MINUTES)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", org_id)
            # Expire running jobs whose heartbeat is too old and pending jobs
            # that were never picked up (created before the stale cutoff)
            await conn.execute(
                """
                UPDATE export_jobs SET status='failed', error_message='job timed out (no heartbeat)'
                WHERE org_id=$1
                  AND status = 'running'
                  AND (last_heartbeat_at IS NULL OR last_heartbeat_at < $2)
                """,
                org_id, stale_cutoff,
            )
            await conn.execute(
                """
                UPDATE export_jobs SET status='failed', error_message='job timed out (never started)'
                WHERE org_id=$1
                  AND status = 'pending'
                  AND created_at < $2
                """,
                org_id, stale_cutoff,
            )
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM export_jobs WHERE org_id=$1 AND status IN ('pending','running')",
                org_id,
            )
    return count
