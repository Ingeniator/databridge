# Export Pipeline — Implemented Contract

**Date**: 2026-06-03 | **Branch**: `002-browser-ui-redesign` (updated from `001-datasink-export`)

## Overview

Export jobs move data from a datasource (connection or system source) to a configured datasink in paginated batches via an ARQ/Redis background worker.

## Datasink Types

| Type | Protocol | Config Required |
|---|---|---|
| `dataset-mock` | HTTP: `GET /datasets`, `POST /datasets`, `POST /datasets/{name}/files` | `url` |
| `annotator-mock` | HTTP: `GET /api/v1/projects`, `POST /api/v1/projects`, `POST /api/v1/projects/{name}/tasks` | `url` |
| `local-zip` | Local filesystem ZIP archive | `path` |
| `local-jsonl` | Local filesystem JSONL file | `path` |

## Job Status Transitions

```
pending → running → completed
                  → failed
```

- **pending**: job created, queued in Redis
- **running**: worker picked up job, processing batches
- **completed**: all records written, sink finalized
- **failed**: unrecoverable error (sink unreachable, disk full, adapter error)

## Role-Based Job Visibility

| X-Role | `role` | Visible Jobs |
|---|---|---|
| `SUPER_ADMIN` | `super_admin` | All jobs (all orgs) |
| `ORG_ADMIN` | `org_admin` | All jobs in caller's org |
| `USER` or absent | `user` | Caller's own jobs only |

Org and user IDs are derived from `X-Group-ID` header (`org_id/user_id` format).

## Stale Job Timeout

Jobs in `running` status with `last_heartbeat_at < NOW() - stale_job_timeout_minutes` are automatically marked `failed`. Default timeout: **15 minutes** (configurable via `export.stale_job_timeout_minutes`).

The sweep runs every 60 seconds as a background asyncio task in the worker process.

## TTL Purge

Jobs with `status IN ('completed', 'failed')` and `completed_at < NOW() - job_ttl_days` are deleted. Default TTL: **7 days** (configurable via `export.job_ttl_days`).

## Per-Org Concurrency Limit

Max concurrent active (pending + running) jobs per org: **5** (configurable via `export.max_concurrent_jobs_per_org`). Enforced atomically via `pg_advisory_xact_lock` on the org_id. Exceeding returns `429 Too Many Requests`.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/datasinks` | List configured datasinks |
| `GET` | `/api/v1/datasinks/{name}/datasets` | List datasets in a sink |
| `POST` | `/api/v1/datasinks/{name}/detect-asset-fields` | Detect URL fields for asset resolution |
| `POST` | `/api/v1/export-jobs` | Create and enqueue a new export job |
| `GET` | `/api/v1/export-jobs` | List jobs (role-filtered, paginated) |
| `GET` | `/api/v1/export-jobs/{id}` | Get single job |
| `POST` | `/api/v1/export-jobs/{id}/retry` | Retry a failed job (creates new job) |
| `GET` | `/api/v1/export-jobs/{id}/download` | Download the output file (local sinks only) |
| `GET` | `/api/v1/connections/{id}/pii-fields` | Returns candidate PII field names for masking |

## File Downloads (Local Sinks)

For `local-zip` and `local-jsonl` sinks, the output file is served via the download endpoint once the job reaches `completed`. Files are written to the path configured in `DatasinkConfig.path` with the naming convention:

```
{destination_dataset}_{job_id}.jsonl   # local-jsonl
{destination_dataset}_{job_id}.zip     # local-zip
```

The job ID in the filename prevents collisions between multiple exports to the same dataset name. Auth is enforced — only the job owner (or org_admin/super_admin) can download. Returns `409` if the job is not yet completed, `404` if the file is missing from disk.

## Data Masking (added 2026-06-03)

When `masking_rules` is non-empty, the worker applies `apply_masking(record, rules)` to each record before writing to the sink. See `specs/current/masking.md` for rule types.

## Sampling (added 2026-06-03)

When `sampling_config` is set, records are filtered through `SamplingBuffer` before sink writes. Skipped records increment `records_skipped`. See `specs/current/sampling.md` for strategy details.

## Webhook (added 2026-06-03)

When `webhook_enabled=true` and `webhook_url` is set, the worker fires a background `POST` to `webhook_url` after `finalise()` completes. Payload: `{job_id, status: "completed", records_processed}`. Fire-and-forget — does not block the worker.

## Worker Record Loop (updated order of operations)

1. Fetch page from adapter
2. For each record: apply **sampling** (skip if dropped) → apply **masking** → resolve **assets** → write to sink

## Metrics (updated)

All Prometheus instruments defined in `export_metrics.py`:

| Metric | Type | Labels |
|---|---|---|
| `export_jobs_created_total` | Counter | `org_id`, `sink_type` |
| `export_jobs_completed_total` | Counter | `org_id`, `sink_type` |
| `export_jobs_failed_total` | Counter | `org_id`, `sink_type` |
| `export_active_jobs` | Gauge | `org_id` |
| `export_records_per_second` | Gauge | `sink_type` |
| `export_asset_resolution_success_total` | Counter | — |
| `export_asset_resolution_failed_total` | Counter | — |
| `export_org_concurrent_jobs` | Gauge | `org_id` |
| `masking_rules_applied_total` | Counter | `org_id` |
| `sampling_records_dropped_total` | Counter | `org_id` |
| `webhook_delivery_total` | Counter | `org_id`, `status` |
| `pii_fields_request_duration_seconds` | Histogram | `connection_type` |
| `preview_request_duration_seconds` | Histogram | `connection_type` |

## Asset Resolution

When `asset_resolution=true`, the worker:
1. Detects URL fields via name convention (`image_url`, `file_url`, etc.) + URL regex on sample values
2. For each URL field in each record: fetches binary content, posts to `asset_sink`, replaces field value with stored filename
3. On fetch failure (4xx/timeout): skips the entire record, increments `asset_errors`
4. Asset dataset auto-named `{destination_dataset}_assets`
