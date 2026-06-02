# Research: Datasink Export Pipeline

**Phase 0 output** | **Date**: 2026-06-02

---

## Decision 1 — Worker IPC: ARQ + Redis

**Decision**: Use ARQ (async Python job queue) with Redis as the broker. The web service enqueues export jobs via `arq.create_pool`; the worker container runs `arq.Worker` polling Redis. ARQ was explicitly removed in Phase 1 (not needed), but re-added here per Constitution §VI: *"Redis-backed queue is required for export; synchronous fallback is development-only."* A `dev` mode synchronous fallback (in-process `asyncio.Task`) is acceptable only when `server.debug=true`.

**Rationale**: ARQ is the canonical async Python job queue — it was the original choice in `pyproject.toml` before Phase 1 stripped it. It supports heartbeats, job abort, retries, and job metadata natively. Redis is already available in the ai-suite docker-compose stack.

**Job flow**:
1. API creates `export_jobs` PostgreSQL row (status=`pending`), then enqueues ARQ job with `job_id = str(export_job_uuid)` so the ARQ job ID matches the DB record
2. Worker picks up the ARQ job, updates DB row status → `running`, processes in batches, emits progress updates
3. Worker writes final status (`completed` or `failed`) to DB
4. UI polls `GET /api/v1/export-jobs/{id}` every 3 s (configurable)

**Per-org concurrency check**: Performed atomically in the API handler via `SELECT COUNT(*) FROM export_jobs WHERE org_id = $1 AND status IN ('pending', 'running')` under a row-level advisory lock (`pg_advisory_xact_lock`). Rejects with 429 if count ≥ limit.

**Alternatives considered**:
- PostgreSQL-based polling (`SELECT FOR UPDATE SKIP LOCKED`): eliminates Redis dependency but adds polling overhead and lacks ARQ's built-in heartbeat/timeout signals
- Celery: heavier, requires message broker setup; ARQ is already in the Python ecosystem and integrates cleanly with asyncio

**New dependency**: `arq>=0.26` added to `pyproject.toml`; `redis[hiredis]>=5.0` also added.

---

## Decision 2 — Auth Extension: Three-Role Model via X-Role Header

**Decision**: Extend `AuthContext` with `org_id`, `user_id`, and `role` fields derived from the existing `X-Group-ID` (format: `org_id/user_id`) and a new `X-Role` header (values: `SUPER_ADMIN`, `ORG_ADMIN`, `USER`). The existing `public_key` field keeps its value (`org_id/user_id` joined) for backwards compatibility with existing connection-scoped queries. The new `role` field drives export job visibility:

| X-Role value | role | Visibility |
|---|---|---|
| `SUPER_ADMIN` | `super_admin` | all jobs |
| `ORG_ADMIN` | `org_admin` | all jobs in same `org_id` |
| `USER` (or absent) | `user` | own jobs only (`user_id` match) |

**X-Group-ID parsing**: Split on the first `/`; first segment = `org_id`, remainder = `user_id`. If header is missing in debug mode, `org_id="dev"`, `user_id="dev"`, `role="super_admin"`.

**AuthContext** (extended — backwards-compatible via NamedTuple defaults):
```python
class AuthContext(NamedTuple):
    public_key: str         # "org_id/user_id" — backwards compat
    is_org_admin: bool      # True when role in (org_admin, super_admin) — backwards compat
    org_id: str = ""
    user_id: str = ""
    role: str = "user"      # "super_admin" | "org_admin" | "user"
```

**Alternatives considered**:
- JWT token introspection: correct for multi-tenant cloud, out of scope (spec assumption §Assumptions: "Auth context is conveyed via X-GROUP-ID header")
- Separate X-Org-ID + X-User-ID headers: more explicit but X-Group-ID format is already established by the gateway

---

## Decision 3 — Datasink Configuration: YAML `datasinks` Section

**Decision**: Add a `datasinks` list to `config.yaml`, analogous to `datasources`. Each entry produces a `DatasinkConfig` frozen dataclass loaded at startup. Datasinks are read-only at runtime (no DB table). The API endpoint `GET /api/v1/datasinks` returns the configured list.

**Config shape**:
```yaml
datasinks:
  - name: "prod-dataset-mock"
    type: dataset-mock
    url: "http://dataset-mock:8020"

  - name: "prod-annotator-mock"
    type: annotator-mock
    url: "http://annotator-mock:8010"

  - name: "local-exports-zip"
    type: local-zip
    path: "/exports"
    filename_template: "{id}_{timestamp}.json"

  - name: "local-exports-jsonl"
    type: local-jsonl
    path: "/exports/output.jsonl"
```

**DatasinkConfig** (frozen dataclass):
```python
@dataclass(frozen=True)
class DatasinkConfig:
    name: str
    type: str               # dataset-mock | annotator-mock | local-zip | local-jsonl
    url: str = ""           # for service sinks
    path: str = ""          # for local sinks (file/dir path in worker container)
    filename_template: str  = ""  # ZIP sink only; falls back to content hash when empty
```

**Validation**: type must be one of the four known values; `url` required for service sinks; `path` required for local sinks. Strict key validation (unknown keys → ValueError at startup).

---

## Decision 4 — Export Settings: Extended `Settings` Dataclass

**Decision**: Add an `ExportSettings` sub-dataclass to hold all export-related tunables, loaded from a new `export:` YAML section.

```python
@dataclass(frozen=True)
class ExportSettings:
    stale_job_timeout_minutes: int = 15
    max_concurrent_jobs_per_org: int = 5
    job_ttl_days: int = 7
    poll_interval_seconds: int = 3       # client-side polling default (served via /api/v1/ui-config)
    keepalive_interval_minutes: int = 2  # worker emits keep-alive when batch takes longer
    batch_size: int = 100                # records fetched per batch from datasource
    redis_url: str = "redis://localhost:6379"
```

All tunables configurable via `config.yaml` `export:` section; defaults match spec requirements.

---

## Decision 5 — Exportable Adapter Protocol: Paginated Fetch + Count

**Decision**: Define a new `ExportableAdapter` Protocol in `adapters.py` with two additional methods: `count()` (returns total matching records) and `fetch_page()` (returns one page with offset). Existing adapters that support export implement this protocol; those that don't (e.g., `DatasetSinkConnectionAdapter`) are not exportable.

```python
class ExportableAdapter(Protocol):
    async def count(self, query: str, start: datetime | None, end: datetime | None) -> int: ...
    async def fetch_page(
        self, query: str, start: datetime | None, end: datetime | None,
        limit: int, offset: int
    ) -> list[dict]: ...
```

**Per-adapter implementation**:
- **ClickHouse**: `SELECT COUNT(*)` for count; `LIMIT {limit} OFFSET {offset}` for pages
- **Trino**: `SELECT COUNT(*)` via statement API; paginated with Trino's native cursor next URI (offset-based fall-back where cursor is unavailable)
- **Langfuse**: `/api/public/traces?page=N&limit=M` native pagination; count from `meta.total`
- **S3**: DuckDB `COUNT(*)` query for count; `LIMIT N OFFSET M` for pages; run via `asyncio.to_thread`

`BaseAdapter` provides a default `count()` that raises `NotImplementedError`; concrete adapters override it.

**Rationale**: Avoids modifying the existing `preview()` interface (preview has a hard 200-record cap and is UI-focused). Export needs full dataset iteration with progress tracking.

---

## Decision 6 — Asset URL Auto-Detection

**Decision**: Implement `detect_asset_url_fields(schema: dict[str, SchemaField], sample_records: list[dict]) -> list[str]` in `src/databridge/export/asset.py`.

Detection rules (OR logic — field included if any rule matches):
1. **Name convention**: field name or its last path segment (after final `.`) matches any of: `url`, `file_url`, `image_url`, `asset_url`, `media_url`, `thumbnail_url`, `download_url`
2. **URL pattern**: the field's `example` value in the schema (or any value sampled from `sample_records`) matches `^https?://` regex

All detected fields are returned pre-selected; user confirms or adjusts in the UI.

---

## Decision 7 — Stale Job Sweep: Background asyncio Task in Worker

**Decision**: The worker process runs a periodic asyncio background task (`sweep_stale_jobs`) every 60 seconds. It queries `SELECT id FROM export_jobs WHERE status = 'running' AND last_heartbeat_at < NOW() - INTERVAL '{stale_timeout} minutes'` and marks matching jobs as `failed` with `error_message = "job timed out — worker did not respond within {stale_timeout} minutes"`.

Workers emit keep-alive updates (unchanged progress counters + `last_heartbeat_at` refresh) at `keepalive_interval_minutes` (default: 2 min) when a batch takes longer than that interval.

**Alternatives considered**:
- Separate sweep service: more isolation but adds operational complexity for what is a short cron-style task
- ARQ's built-in job timeout: ARQ supports `timeout` per job, but it kills the coroutine; a DB-level sweep is more observable and works even if ARQ/Redis restarts

---

## Decision 8 — Local Sink Paths: Shared Volume + Config

**Decision**: Local sinks (`local-zip`, `local-jsonl`) write to paths configured in `DatasinkConfig.path`. In docker-compose, this maps to a shared named volume mounted at the same path in both the worker container and any downstream consumer. The path must exist and be writable at job start; if not, the job fails immediately with a clear error.

**ZIP sink naming**: final filename is `{destination_dataset}_{job_id}.zip`, placed in the configured directory.
**JSONL sink naming**: final filename is `{destination_dataset}_{job_id}.jsonl`, placed in the configured directory (or the exact path if `path` is a file).

---

## Decision 9 — Dataset-Mock / Annotator-Mock Protocol

**Decision**: Implement the unified write protocol (list-datasets, create-dataset, post-file) as concrete `BaseSink` subclasses. The annotator-mock sink wraps its native API via an internal adapter layer — no changes to the annotator-mock service itself.

**Dataset-mock unified protocol** (already matches):
- `GET {url}/datasets` → `{"datasets": ["name1", ...]}`
- `POST {url}/datasets` with `{"name": "dataset_name"}` → 201
- `POST {url}/datasets/{dataset}/files` with JSON body → 201

**Annotator-mock adapter mapping** (translated internally):
- list-datasets: `GET {url}/api/v1/projects` → extract project names
- create-dataset: `POST {url}/api/v1/projects` with `{"name": "dataset_name"}`
- post-file: `POST {url}/api/v1/projects/{dataset}/tasks` with the record payload

---

## Decision 10 — Prometheus Metrics: Export-Specific Counters and Gauges

**Decision**: Add the following Prometheus instruments to `metrics.py` (exported from a new `export_metrics.py` module to avoid circular imports):

```python
export_jobs_created_total   # Counter, labels: [org_id, sink_type]
export_jobs_completed_total # Counter, labels: [org_id, sink_type]
export_jobs_failed_total    # Counter, labels: [org_id, sink_type]
export_active_jobs          # Gauge, labels: [org_id]
export_records_per_second   # Gauge, labels: [sink_type]  (updated per batch)
export_asset_resolution_success_total  # Counter, no labels
export_asset_resolution_failed_total   # Counter, no labels
export_org_concurrent_jobs  # Gauge, labels: [org_id]
```

Worker updates gauges during processing. API handler increments `export_jobs_created_total` on job creation. Worker increments `export_jobs_completed_total` / `export_jobs_failed_total` on completion.

**Multiprocess safety**: Worker and API run in separate processes; use `PROMETHEUS_MULTIPROC_DIR` with `multiprocess.MultiProcessCollector` (already wired in `metrics.py`).
