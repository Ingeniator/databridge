# Field Extraction — Implemented Contract

**Date**: 2026-07-13 | **Feature**: 004-trace-extraction

## Overview

Field extraction is an opt-in step in the export pipeline that replaces an entire record with the JSON value found at a single configured nested field path, instead of exporting the surrounding envelope. It exists for envelope-shaped sources (e.g. Amplitude-style events, where the meaningful payload — a trace, or any other structured content — is buried inside a field such as `event_properties`) where the raw envelope is not what the destination dataset should contain.

Unlike masking, which mutates a field in place, field extraction replaces the record's entire shape. It runs as its own pipeline stage, before masking, so masking rules protect whatever content is actually exported rather than a discarded envelope.

## Field Path Resolution

`extract_field_value(record, field_path) -> Any | None`

1. Splits `field_path` on `.` and walks each segment via `resolve_field_path`, transparently `json.loads`-ing any string container encountered along the way (a `event_properties` field stored as a JSON-encoded string is descended into just like a native dict).
2. A path segment that is a plain digit also indexes into a list (e.g. `items.0.trace`).
3. If the path doesn't resolve (missing key, out-of-range index, non-container node), returns `None`.
4. If the resolved value is a native `dict`/`list`, it's returned unchanged.
5. If the resolved value is a `str`, it's parsed via `json.loads`; the parsed result is returned only if it's a `dict`/`list` — a bare JSON scalar (`"123"`, `"true"`) or a plain non-JSON string both return `None`.

The single-segment descend step (`resolve_field_path` → `_decode_and_index`) is shared with masking's `_apply_at_path`, which reuses it for its own dotted-path/list-index resolution before mutating. See `src/databridge/export/extraction.py`.

## Worker Integration

Field extraction runs per-record during the batch loop, immediately after the sampling filter and before masking, when `field_extraction = true` on the job:

1. `extract_field_value(record, field_extraction_path)` is called.
2. If it returns `None`: the record is skipped (`records_skipped++`), `export_field_extraction_failed_total` increments, and the loop continues to the next record.
3. If it returns a value: `record` is reassigned to that value, `export_field_extraction_success_total` increments, and the (now-replaced) record proceeds to masking, asset resolution, and the sink.

**Configuration validation**: `field_extraction=true` with an empty/missing `field_extraction_path` is rejected at job creation (`422`) via a Pydantic model validator — it is not silently accepted as a no-op, since it would deterministically skip every record.

## API Endpoint

`POST /api/v1/connections/{id}/test-field-extraction`

Previews whether a field path resolves against a few sample records from a datasource, without starting an export job. Used by the UI's "Test Extraction" button to validate a field path before running a full export.

**Request body** (`FieldExtractionTestRequest`):

| Field | Type | Notes |
|---|---|---|
| `field_path` | `string` | 1–255 chars |

**Response** (`FieldExtractionTestResponse`):

| Field | Type |
|---|---|
| `results` | `list[FieldExtractionTestResult]` |

Each result: `resolved: bool`, `value_preview: string | null` (present when resolved), `error: string | null` (present when not resolved).

**Error responses**:

| Status | Condition |
|---|---|
| `404` | connection not found |
| `502` | preview fetch from the underlying datasource failed |

Internally calls `adapter.preview("", None, None, limit=5)` then `extract_field_value` per sample record — same adapter-resolution branch (system source vs. DB connection) as `test-asset-resolution`.

## Metrics

| Metric | Type | Labels | Incremented when |
|---|---|---|---|
| `export_field_extraction_success_total` | Counter | — | A record's field path resolved to usable content |
| `export_field_extraction_failed_total` | Counter | — | A record's field path did not resolve (missing, empty, non-JSON string, or bare JSON scalar) |

## Constraints

- Exactly one field path per job — not a list, unlike `asset_url_fields`.
- Extraction is one-to-one: one source record yields at most one exported record, never a fan-out of multiple records from one source record.
- "Usable content" is strictly JSON-parseable (`dict`/`list`) — a plain non-JSON string at the field path is treated the same as a missing field, not passed through as-is.
