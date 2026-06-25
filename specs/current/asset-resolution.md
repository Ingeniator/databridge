# Asset Resolution — Implemented Contract

**Date**: 2026-06-03 | **Feature**: 004

## Overview

Asset resolution is an opt-in step in the export pipeline that replaces URL-valued record fields with binary assets fetched from remote URLs and posted to a designated asset datasink. The result is a self-contained export where assets travel alongside structured records.

## Field Detection

`detect_asset_url_fields(schema, sample_records) → list[str]`

Applies three heuristics in order; first match wins per field:

| Priority | Rule |
|---|---|
| 1 | Leaf name (last `.`-segment) is in the known set (case-insensitive): `url`, `file_url`, `image_url`, `asset_url`, `media_url`, `thumbnail_url`, `download_url` |
| 2 | Schema `example` value is a string matching `^https?://` |
| 3 | Any value in `sample_records` for the field matches `^https?://` |

Input `schema` is a flat `dict[str, dict]` keyed by dotted field path (e.g. `"meta.image_url"`). Sample record lookup tries both the full dotted path and the leaf name.

Returns deduplicated `list[str]` of matching field paths. Fields failing all three rules are excluded.

## Asset Resolution

`resolve_assets(record, url_fields, url_prefix, asset_sink, asset_dataset) → dict`

For each field in `url_fields`:

1. Skip if field value is absent/empty.
2. Build URL: `url_prefix + str(value)` when `url_prefix` is non-empty, otherwise `str(value)` as-is.
3. Fetch binary content via `httpx.AsyncClient` with a **30-second timeout**.
4. On HTTP status ≥ 400: raise `AssetResolutionError`.
5. On `httpx.RequestError` (network failure, timeout): raise `AssetResolutionError`.
6. Extract filename from the last `/`-segment of the URL; fall back to `"asset"` if empty.
7. Post to the asset sink: `asset_sink.post_file(asset_dataset, {"data": content.hex(), "source_url": url}, filename)`.
8. Replace the field value in the record with `filename`.

Returns the updated record copy. The original record dict is not mutated.

## Worker Integration

Asset resolution runs per-record during the batch loop when all of the following are true:

- `asset_resolution = true` on the job
- `asset_sink` is resolved and reachable
- `asset_url_fields` is non-empty
- `asset_dataset` is set

Asset dataset name is auto-derived at job creation time as `{destination_dataset}_assets`.

A separate `asset_datasink_name` can be specified; if absent, the same datasink as the main export is used (but with the `_assets` dataset).

**Error handling**: any exception during resolution (including `AssetResolutionError`) causes the record to be skipped (`records_skipped++`, `asset_errors++`). Processing continues with the next record.

## API Endpoint

`POST /api/v1/datasinks/{name}/detect-asset-fields`

Detects URL fields from a datasource without starting an export job. Used by the UI to pre-populate `asset_url_fields` when configuring a job.

**Request body** (`AssetFieldDetectRequest`):

| Field | Type | Notes |
|---|---|---|
| `connection_id` | `UUID \| null` | Mutually exclusive with `system_source_name` |
| `system_source_name` | `string \| null` | Mutually exclusive with `connection_id` |

Exactly one field must be provided.

**Response** (`AssetFieldDetectResponse`):

| Field | Type |
|---|---|
| `candidate_fields` | `list[string]` |

**Error responses**:

| Status | Condition |
|---|---|
| `400` | Neither field provided |
| `400` | Both fields provided |
| `404` | `system_source_name` not found in config |
| `501` | `connection_id` provided (not yet implemented — use `system_source_name`) |

Internally calls `adapter.schema()` + `adapter.preview(limit=20)` then runs `detect_asset_url_fields`.

## Metrics

| Metric | Type | Labels | Incremented when |
|---|---|---|---|
| `export_asset_resolution_success_total` | Counter | — | Record resolved successfully |
| `export_asset_resolution_failed_total` | Counter | — | Resolution fails (any exception) |

## Constraints

- Only HTTP/HTTPS URLs are supported. Non-URL field values are skipped silently.
- Asset content is transmitted as hex-encoded string in the sink payload (`"data": content.hex()`).
- `url_prefix` is a plain string prefix, not a base URL — no slash is inserted automatically.
- Filename collisions across records are not handled; last write wins in the asset dataset.
