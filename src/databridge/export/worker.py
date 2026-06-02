from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import UUID

import structlog

from databridge.export.db import (
    get_export_job,
    update_export_job_status,
    update_export_progress,
    update_records_total,
)
from databridge.export.models import ExportJobStatus
from databridge.export_metrics import (
    EXPORT_ACTIVE_JOBS,
    EXPORT_ASSET_RESOLUTION_FAILED,
    EXPORT_ASSET_RESOLUTION_SUCCESS,
    EXPORT_JOBS_COMPLETED,
    EXPORT_JOBS_FAILED,
    EXPORT_RECORDS_PER_SECOND,
)

logger = structlog.get_logger(__name__)


async def run_export_job(ctx: dict, job_id: str) -> None:
    pool = ctx["pool"]
    settings = ctx["settings"]

    # Load job
    job_resp = await pool.fetchrow("SELECT * FROM export_jobs WHERE id = $1", UUID(job_id))
    if job_resp is None:
        logger.warning("export_job_not_found", job_id=job_id)
        return

    org_id = job_resp["org_id"]
    datasink_name = job_resp["datasink_name"]

    # Find datasink config
    datasink_config = next(
        (s for s in settings.datasinks if s.name == datasink_name), None
    )
    if datasink_config is None:
        await update_export_job_status(
            pool, UUID(job_id), ExportJobStatus.failed,
            error_message=f"datasink '{datasink_name}' not found in config"
        )
        return

    try:
        await update_export_job_status(pool, UUID(job_id), ExportJobStatus.running)
        EXPORT_ACTIVE_JOBS.labels(org_id=org_id).inc()

        # Get adapter
        import json as _json

        datasource_type = job_resp["datasource_type"]
        datasource_ref = job_resp["datasource_ref"]
        filter_raw = job_resp["datasource_filter"]
        if isinstance(filter_raw, str):
            filter_raw = _json.loads(filter_raw)
        query = (filter_raw or {}).get("query", "")
        start_str = (filter_raw or {}).get("start")
        end_str = (filter_raw or {}).get("end")
        start = datetime.fromisoformat(start_str) if start_str else None
        end = datetime.fromisoformat(end_str) if end_str else None

        if datasource_type == "connection":
            conn_row = await pool.fetchrow(
                "SELECT * FROM connections WHERE id = $1", UUID(datasource_ref)
            )
            if conn_row is None:
                raise ValueError(f"connection {datasource_ref!r} not found")
            from databridge.adapters import get_adapter
            from databridge.crypto import decrypt
            creds = _json.loads(decrypt(bytes(conn_row["credentials_enc"])))
            adapter = get_adapter(dict(conn_row), creds)
        else:
            # system source — match by name or deterministic UUID
            cfg = next(
                (s for s in settings.datasources
                 if s.name == datasource_ref or str(s.id) == datasource_ref),
                None,
            )
            if cfg is None:
                raise ValueError(f"system source {datasource_ref!r} not found in config")
            from databridge.adapters import get_adapter
            adapter = get_adapter(cfg, {})

        # Count total
        total = await adapter.count(query, start, end)
        await update_records_total(pool, UUID(job_id), total)

        # Set up sink
        from databridge.sinks import get_sink
        sink = get_sink(datasink_config)
        await sink.ping()
        destination_dataset = job_resp["destination_dataset"]
        await sink.create_dataset(destination_dataset)

        # Asset resolution setup
        asset_resolution = job_resp["asset_resolution"]
        asset_sink = None
        asset_dataset = None
        if asset_resolution:
            asset_url_fields_raw = job_resp["asset_url_fields"]
            if isinstance(asset_url_fields_raw, str):
                asset_url_fields_raw = _json.loads(asset_url_fields_raw)
            asset_url_fields = asset_url_fields_raw or []
            asset_url_prefix = job_resp["asset_url_prefix"] or ""
            asset_datasink_name = job_resp["asset_datasink_name"]
            asset_dataset = job_resp["asset_dataset"]
            if asset_datasink_name and asset_dataset:
                asset_cfg = next(
                    (s for s in settings.datasinks if s.name == asset_datasink_name), None
                )
                if asset_cfg:
                    asset_sink = get_sink(asset_cfg)
                    await asset_sink.ping()
                    await asset_sink.create_dataset(asset_dataset)

        # Batch loop
        batch_size = settings.export.batch_size
        keepalive_interval = settings.export.keepalive_interval_minutes * 60
        records_processed = 0
        records_skipped = 0
        asset_errors = 0
        batch_start = time.monotonic()

        for offset in range(0, total, batch_size):
            records = await adapter.fetch_page(query, start, end, limit=batch_size, offset=offset)
            for record in records:
                if asset_resolution and asset_sink and asset_url_fields and asset_dataset:
                    try:
                        from databridge.export.asset import resolve_assets, AssetResolutionError
                        record = await resolve_assets(
                            record, asset_url_fields, asset_url_prefix, asset_sink, asset_dataset
                        )
                        EXPORT_ASSET_RESOLUTION_SUCCESS.inc()
                    except Exception:
                        records_skipped += 1
                        asset_errors += 1
                        EXPORT_ASSET_RESOLUTION_FAILED.inc()
                        continue

                await sink.post_file(destination_dataset, record)
                records_processed += 1

            if hasattr(sink, "records_skipped"):
                records_skipped += sink.records_skipped

            await update_export_progress(pool, UUID(job_id), records_processed, records_skipped, asset_errors)

            # Throughput metric
            elapsed = time.monotonic() - batch_start
            if elapsed > 0:
                EXPORT_RECORDS_PER_SECOND.labels(sink_type=datasink_config.type).set(
                    records_processed / elapsed
                )

            # Keep-alive check
            if (time.monotonic() - batch_start) > keepalive_interval:
                batch_start = time.monotonic()

        await sink.finalise()
        if asset_sink:
            await asset_sink.finalise()

        await update_export_job_status(pool, UUID(job_id), ExportJobStatus.completed)
        EXPORT_JOBS_COMPLETED.labels(org_id=org_id, sink_type=datasink_config.type).inc()
        EXPORT_ACTIVE_JOBS.labels(org_id=org_id).dec()

    except Exception as exc:
        logger.error("export_job_failed", job_id=job_id, exc_info=True)
        await update_export_job_status(
            pool, UUID(job_id), ExportJobStatus.failed, error_message=str(exc)
        )
        EXPORT_JOBS_FAILED.labels(org_id=org_id, sink_type=datasink_config.type if datasink_config else "unknown").inc()
        EXPORT_ACTIVE_JOBS.labels(org_id=org_id).dec()


