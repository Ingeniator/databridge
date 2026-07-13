from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import UUID

import structlog
from arq.connections import RedisSettings

from databridge.export.db import (
    get_export_job,
    is_job_cancelled,
    update_export_job_status,
    update_export_progress,
    update_external_asset_dataset_id,
    update_external_dataset_id,
    update_records_total,
    _parse_masking_rules,
    _parse_sampling_config,
)
from databridge.export.models import ExportJobStatus
from databridge.export_metrics import (
    EXPORT_ACTIVE_JOBS,
    EXPORT_ASSET_RESOLUTION_FAILED,
    EXPORT_ASSET_RESOLUTION_SUCCESS,
    EXPORT_FIELD_EXTRACTION_FAILED,
    EXPORT_FIELD_EXTRACTION_SUCCESS,
    EXPORT_JOBS_COMPLETED,
    EXPORT_JOBS_FAILED,
    EXPORT_RECORDS_PER_SECOND,
    MASKING_RULES_APPLIED,
    SAMPLING_RECORDS_DROPPED,
    WEBHOOK_DELIVERY,
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
    if job_resp["status"] == "cancelled":
        logger.info("export_job_skipped_cancelled", job_id=job_id)
        return

    org_id = job_resp["org_id"]
    user_id = job_resp["user_id"]
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

    webhook_url: str | None = None
    webhook_enabled: bool = False
    webhook_payload_template: str | None = None
    records_processed: int = 0
    records_skipped: int = 0

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
        # The browser auto-detects a timestamp field client-side and sends it here;
        # it's not necessarily saved as the connection's timestamp_column credential
        # (that only happens if the user explicitly pins it), so it must override
        # the credential per-job or the adapter falls back to a hardcoded "timestamp"
        # column that may not exist on this source.
        time_field = (filter_raw or {}).get("time_field")

        if datasource_type == "connection":
            conn_row = await pool.fetchrow(
                "SELECT * FROM connections WHERE id = $1", UUID(datasource_ref)
            )
            if conn_row is None:
                raise ValueError(f"connection {datasource_ref!r} not found")
            from databridge.adapters import apply_time_field_override, get_adapter
            from databridge.crypto import decrypt_credentials
            creds = decrypt_credentials(bytes(conn_row["credentials_enc"]))
            adapter = get_adapter(dict(conn_row), creds)
            adapter, creds = apply_time_field_override(adapter, dict(conn_row), creds, time_field)
        else:
            # system source — match by name or deterministic UUID
            cfg = next(
                (s for s in settings.datasources
                 if s.name == datasource_ref or str(s.id) == datasource_ref),
                None,
            )
            if cfg is None:
                raise ValueError(f"system source {datasource_ref!r} not found in config")
            from databridge.adapters import apply_time_field_override, get_adapter
            adapter = get_adapter(cfg, {})
            adapter, _ = apply_time_field_override(adapter, cfg, {}, time_field)

        # Count total
        total = await adapter.count(query, start, end)
        await update_records_total(pool, UUID(job_id), total)

        # Set up sink
        from databridge.sinks import get_sink
        sink = get_sink(datasink_config)
        sink._job_id = job_id          # stamp so filename includes job ID
        sink.set_actor(org_id, user_id)
        await sink.ping()
        destination_dataset = job_resp["destination_dataset"]
        await sink.create_dataset(destination_dataset)
        if sink.external_id:
            await update_external_dataset_id(pool, UUID(job_id), sink.external_id)

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
                    asset_sink._job_id = job_id
                    asset_sink.set_actor(org_id, user_id)
                    await asset_sink.ping()
                    await asset_sink.create_dataset(asset_dataset)
                    ext_asset_id = getattr(asset_sink, "_dataset_ids", {}).get(asset_dataset)
                    if ext_asset_id:
                        await update_external_asset_dataset_id(pool, UUID(job_id), ext_asset_id)

        # Load masking/sampling/webhook config from job row
        masking_rules = _parse_masking_rules(job_resp.get("masking_rules"))
        sampling_config_obj = _parse_sampling_config(job_resp.get("sampling_config"))
        field_extraction = job_resp.get("field_extraction", False)
        field_extraction_path = job_resp.get("field_extraction_path") or ""
        webhook_url = job_resp.get("webhook_url")
        webhook_enabled = job_resp.get("webhook_enabled", False)
        webhook_payload_template = job_resp.get("webhook_payload_template")

        sampling_buffer = None
        max_items = sampling_config_obj.max_items if sampling_config_obj is not None else None
        if sampling_config_obj is not None:
            from databridge.export.sampling import SamplingBuffer
            sampling_buffer = SamplingBuffer(sampling_config_obj)

        # Batch loop
        batch_size = settings.export.batch_size
        keepalive_interval = settings.export.keepalive_interval_minutes * 60
        asset_errors = 0
        batch_start = time.monotonic()

        _limit_reached = False
        for offset in range(0, total, batch_size):
            if _limit_reached:
                break
            records = await adapter.fetch_page(query, start, end, limit=batch_size, offset=offset)
            logger.info("export_batch_fetched", job_id=job_id, offset=offset, batch_size=batch_size, returned=len(records))
            for record in records:
                # Sampling filter
                if sampling_buffer is not None:
                    if not sampling_buffer.feed(record):
                        records_skipped += 1
                        SAMPLING_RECORDS_DROPPED.labels(org_id=org_id).inc()
                        continue

                # Field extraction (must run before masking so masking rules
                # protect the record that actually reaches the sink)
                if field_extraction:
                    from databridge.export.extraction import extract_field_value
                    extracted = extract_field_value(record, field_extraction_path)
                    if extracted is None:
                        records_skipped += 1
                        EXPORT_FIELD_EXTRACTION_FAILED.inc()
                        continue
                    record = extracted
                    EXPORT_FIELD_EXTRACTION_SUCCESS.inc()

                # Masking
                if masking_rules:
                    from databridge.export.masking import apply_masking
                    record = apply_masking(record, masking_rules)
                    MASKING_RULES_APPLIED.labels(org_id=org_id).inc()

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

                if max_items and records_processed >= max_items:
                    _limit_reached = True
                    break

            await update_export_progress(pool, UUID(job_id), records_processed, records_skipped, asset_errors)

            if await is_job_cancelled(pool, UUID(job_id)):
                _limit_reached = True
                break

            # Throughput metric
            elapsed = time.monotonic() - batch_start
            if elapsed > 0:
                EXPORT_RECORDS_PER_SECOND.labels(sink_type=datasink_config.type).set(
                    records_processed / elapsed
                )

            # Keep-alive check
            if (time.monotonic() - batch_start) > keepalive_interval:
                batch_start = time.monotonic()

        if hasattr(sink, "records_skipped"):
            records_skipped += sink.records_skipped
        await sink.finalise()
        if sink.external_id:
            await update_external_dataset_id(pool, UUID(job_id), sink.external_id)
        if asset_sink:
            await asset_sink.finalise()
            if asset_sink.external_id:
                await update_external_asset_dataset_id(pool, UUID(job_id), asset_sink.external_id)

        if await is_job_cancelled(pool, UUID(job_id)):
            EXPORT_ACTIVE_JOBS.labels(org_id=org_id).dec()
            return

        await update_export_job_status(pool, UUID(job_id), ExportJobStatus.completed)
        EXPORT_JOBS_COMPLETED.labels(org_id=org_id, sink_type=datasink_config.type).inc()
        EXPORT_ACTIVE_JOBS.labels(org_id=org_id).dec()

        # Webhook on completion
        if webhook_enabled and webhook_url:
            from databridge.export.webhook import deliver_webhook, render_payload
            import asyncio as _asyncio
            _download_url = _build_download_url(settings, job_id, datasink_config)
            _assets_url = (
                _build_download_url(settings, job_id, datasink_config, assets=True)
                if asset_resolution else ""
            )
            _ctx = {
                "job_id": job_id,
                "status": "completed",
                "org_id": org_id,
                "destination_dataset": job_resp.get("destination_dataset", ""),
                "records_processed": records_processed,
                "records_skipped": records_skipped,
                "error": "",
                "download_url": _download_url,
                "assets_download_url": _assets_url,
            }
            _asyncio.create_task(deliver_webhook(webhook_url, render_payload(webhook_payload_template, _ctx)))
            WEBHOOK_DELIVERY.labels(org_id=org_id, status="success").inc()

    except Exception as exc:
        logger.error("export_job_failed", job_id=job_id, exc_info=True)
        await update_export_job_status(
            pool, UUID(job_id), ExportJobStatus.failed, error_message=str(exc)
        )
        EXPORT_JOBS_FAILED.labels(org_id=org_id, sink_type=datasink_config.type if datasink_config else "unknown").inc()
        EXPORT_ACTIVE_JOBS.labels(org_id=org_id).dec()

        if webhook_enabled and webhook_url:
            from databridge.export.webhook import deliver_webhook, render_payload
            import asyncio as _asyncio
            _ctx = {
                "job_id": job_id,
                "status": "failed",
                "org_id": org_id,
                "destination_dataset": job_resp.get("destination_dataset", "") if job_resp else "",
                "records_processed": records_processed,
                "records_skipped": records_skipped,
                "error": str(exc),
                "download_url": "",
                "assets_download_url": "",
            }
            _asyncio.create_task(deliver_webhook(webhook_url, render_payload(webhook_payload_template, _ctx)))
            WEBHOOK_DELIVERY.labels(org_id=org_id, status="failure").inc()


_LOCAL_SINK_TYPES = {"local-zip", "local-jsonl"}


def _build_download_url(settings, job_id: str, datasink_config, assets: bool = False) -> str:
    if datasink_config is None:
        return ""
    if datasink_config.type in _LOCAL_SINK_TYPES:
        base = settings.server.public_url.rstrip("/")
        path = f"/api/v1/export-jobs/{job_id}/download"
        if assets:
            path += "?assets=true"
        return f"{base}{path}" if base else path
    return datasink_config.url or ""


async def startup(ctx: dict) -> None:
    from databridge.config import get_settings
    from databridge.db.pool import create_pool
    from databridge.logging_config import setup_logging
    settings = get_settings()
    setup_logging(debug=settings.server.debug, silence_probes=settings.server.silence_probes)
    ctx["pool"] = await create_pool()
    ctx["settings"] = settings


async def shutdown(ctx: dict) -> None:
    await ctx["pool"].close()


def _redis_settings() -> RedisSettings:
    from databridge.config import get_settings
    return RedisSettings.from_dsn(get_settings().export.redis_url)


class WorkerSettings:
    functions = [run_export_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()


