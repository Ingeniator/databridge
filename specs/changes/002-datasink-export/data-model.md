# Data Model: Datasink Export Pipeline

**Phase 1 output** | **Date**: 2026-06-02

---

## 1. Configuration Dataclasses (extensions to `config.py`)

### 1.1 DatasinkConfig

```python
_DATASINK_VALID_KEYS = {
    "name", "type", "url", "path", "filename_template",
}

_DATASINK_SERVICE_TYPES = {"dataset-mock", "annotator-mock"}
_DATASINK_LOCAL_TYPES   = {"local-zip", "local-jsonl"}
_DATASINK_ALL_TYPES     = _DATASINK_SERVICE_TYPES | _DATASINK_LOCAL_TYPES


@dataclass(frozen=True)
class DatasinkConfig:
    name: str
    type: str               # "dataset-mock" | "annotator-mock" | "local-zip" | "local-jsonl"
    url: str = ""           # required for service sinks (dataset-mock, annotator-mock)
    path: str = ""          # required for local sinks (local-zip, local-jsonl)
    filename_template: str = ""  # ZIP sink only; falls back to content hash when ""
```

### 1.2 ExportSettings

```python
@dataclass(frozen=True)
class ExportSettings:
    stale_job_timeout_minutes: int = 15
    max_concurrent_jobs_per_org: int = 5
    job_ttl_days: int = 7
    poll_interval_seconds: int = 3
    keepalive_interval_minutes: int = 2
    batch_size: int = 100
    redis_url: str = "redis://localhost:6379"
```

### 1.3 Extended Settings

```python
@dataclass(frozen=True)
class Settings:
    server: ServerConfig
    database_url: str
    encryption_key: str
    datasources: tuple[SystemSourceConfig, ...]
    datasinks: tuple[DatasinkConfig, ...]        # new in Phase 2
    export: ExportSettings                        # new in Phase 2
```

### 1.4 Config YAML Shape (extended)

```yaml
server:
  host: "0.0.0.0"
  port: 5010
  debug: false
  silence_probes: true
  hide_auth_inputs: false

database_url: "postgresql://postgres:${DB_PASSWORD}@postgres:5432/databridge"
encryption_key: "vault:DATABRIDGE_ENCRYPTION_KEY"

datasources:
  - name: "prod-clickhouse"
    type: clickhouse
    url: "http://clickhouse:8123"
    database: "default"
    table: "llogr_events"
    user: "default"
    password: "vault:CH_PASSWORD"

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
    path: "/exports"

export:
  stale_job_timeout_minutes: 15
  max_concurrent_jobs_per_org: 5
  job_ttl_days: 7
  poll_interval_seconds: 3
  keepalive_interval_minutes: 2
  batch_size: 100
  redis_url: "redis://redis:6379"
```

---

## 2. Auth Extension

Extended `AuthContext` — backwards-compatible via NamedTuple defaults:

```python
class AuthContext(NamedTuple):
    public_key: str         # "org_id/user_id" (original value — backwards compat for connection queries)
    is_org_admin: bool      # True when role in ("org_admin", "super_admin") — backwards compat
    org_id: str = ""        # first segment of X-Group-ID before "/"
    user_id: str = ""       # remainder of X-Group-ID after first "/"
    role: str = "user"      # "super_admin" | "org_admin" | "user"
```

**Role mapping from X-Role header**:

| X-Role header value | `role` | `is_org_admin` |
|---|---|---|
| `SUPER_ADMIN` | `super_admin` | `True` |
| `ORG_ADMIN` | `org_admin` | `True` |
| `USER` or absent | `user` | `False` |

Debug fallback (when `server.debug=true` and no auth header): `org_id="dev"`, `user_id="dev"`, `role="super_admin"`.

---

## 3. Domain Entities

### 3.1 FilterSnapshot (value object)

```python
class FilterSnapshot(BaseModel):
    query: str = ""
    start: datetime | None = None
    end: datetime | None = None
```

Stored as JSONB in `export_jobs.datasource_filter`.

### 3.2 ExportJob (core entity)

```python
class ExportJobStatus(str, Enum):
    pending   = "pending"
    running   = "running"
    completed = "completed"
    failed    = "failed"


class ExportJob(BaseModel):
    """Internal representation — maps 1:1 to export_jobs table row."""
    id: UUID
    org_id: str
    user_id: str
    datasource_type: Literal["connection", "system"]
    datasource_ref: str          # UUID string for connection; config name for system
    datasource_filter: FilterSnapshot
    datasink_name: str           # references DatasinkConfig.name
    destination_dataset: str
    asset_resolution: bool
    asset_url_fields: list[str]
    asset_url_prefix: str
    asset_datasink_name: str | None
    asset_dataset: str | None    # auto-derived: "{destination_dataset}_assets"
    status: ExportJobStatus
    records_total: int | None    # None until worker sets it
    records_processed: int
    records_skipped: int
    asset_errors: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    last_heartbeat_at: datetime | None  # internal; not exposed in API response
```

### 3.3 Datasink (config-only, not persisted in DB)

```python
class DatasinkInfo(BaseModel):
    """API response model for GET /api/v1/datasinks."""
    name: str
    type: str
```

---

## 4. API Request/Response Models

### 4.1 ExportJobCreate

```python
class ExportJobCreate(BaseModel):
    datasource_type: Literal["connection", "system"]
    datasource_ref: str
    datasource_filter: FilterSnapshot = Field(default_factory=FilterSnapshot)
    datasink_name: str
    destination_dataset: Annotated[str, Field(min_length=1, max_length=120)]
    asset_resolution: bool = False
    asset_url_fields: list[str] = Field(default_factory=list)
    asset_url_prefix: str = ""
    asset_datasink_name: str | None = None
```

### 4.2 ExportJobResponse

```python
class ExportJobResponse(BaseModel):
    id: UUID
    org_id: str
    user_id: str
    datasource_type: str
    datasource_ref: str
    datasource_filter: FilterSnapshot
    datasink_name: str
    destination_dataset: str
    asset_resolution: bool
    asset_url_fields: list[str]
    asset_url_prefix: str
    asset_datasink_name: str | None
    asset_dataset: str | None
    status: ExportJobStatus
    records_total: int | None
    records_processed: int
    records_skipped: int
    asset_errors: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
```

### 4.3 ExportJobListResponse

```python
class ExportJobListResponse(BaseModel):
    items: list[ExportJobResponse]
    total: int
    page: int
    page_size: int
```

### 4.4 DatasinkDatasetListResponse

```python
class DatasinkDatasetListResponse(BaseModel):
    datasets: list[str]
```

### 4.5 AssetFieldDetectRequest / Response

```python
class AssetFieldDetectRequest(BaseModel):
    connection_id: UUID | None = None   # for user connections
    system_source_name: str | None = None  # for system sources

class AssetFieldDetectResponse(BaseModel):
    candidate_fields: list[str]   # pre-selected candidates for user review
```

---

## 5. Database Schema

### 5.1 export_jobs table

```sql
CREATE TABLE export_jobs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id              TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    datasource_type     TEXT NOT NULL CHECK (datasource_type IN ('connection', 'system')),
    datasource_ref      TEXT NOT NULL,
    datasource_filter   JSONB NOT NULL DEFAULT '{}',
    datasink_name       TEXT NOT NULL,
    destination_dataset TEXT NOT NULL,
    asset_resolution    BOOLEAN NOT NULL DEFAULT FALSE,
    asset_url_fields    JSONB NOT NULL DEFAULT '[]',
    asset_url_prefix    TEXT NOT NULL DEFAULT '',
    asset_datasink_name TEXT,
    asset_dataset       TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    records_total       INTEGER,
    records_processed   INTEGER NOT NULL DEFAULT 0,
    records_skipped     INTEGER NOT NULL DEFAULT 0,
    asset_errors        INTEGER NOT NULL DEFAULT 0,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    last_heartbeat_at   TIMESTAMPTZ
);

CREATE INDEX idx_export_jobs_org_id      ON export_jobs (org_id);
CREATE INDEX idx_export_jobs_user_id     ON export_jobs (user_id);
CREATE INDEX idx_export_jobs_status      ON export_jobs (status);
CREATE INDEX idx_export_jobs_created_at  ON export_jobs (created_at DESC);
```

Managed via a new Alembic migration script.

---

## 6. Adapter Protocol Extension

```python
class ExportableAdapter(Protocol):
    """Extension of ConnectionAdapter for datasources that support full export iteration."""

    async def count(
        self,
        query: str,
        start: datetime | None,
        end: datetime | None,
    ) -> int:
        """Return total records matching the filter."""
        ...

    async def fetch_page(
        self,
        query: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        """Return one page of records."""
        ...
```

**Per-adapter implementation strategy**:

| Adapter | count() | fetch_page() |
|---------|---------|-------------|
| ClickHouse | `SELECT COUNT(*) FROM {table} WHERE ...` | `SELECT * ... LIMIT {limit} OFFSET {offset}` |
| Trino | `SELECT COUNT(*) ...` via statement API | LIMIT/OFFSET in SQL statement |
| Langfuse | `/api/public/traces?limit=1` → `meta.total` | `/api/public/traces?page={page}&limit={limit}` |
| S3 | `SELECT COUNT(*) FROM reader(...)` via DuckDB | `SELECT * ... LIMIT {limit} OFFSET {offset}` via DuckDB |

---

## 7. Sink Abstraction

### 7.1 BaseSink (ABC in `src/databridge/sinks/base.py`)

```python
from abc import ABC, abstractmethod


class BaseSink(ABC):
    """Abstract base class for all export sink implementations."""

    def __init__(self, config: DatasinkConfig) -> None:
        self._config = config

    @abstractmethod
    async def ping(self) -> None:
        """Check that the sink is reachable. Raise on failure."""
        ...

    @abstractmethod
    async def list_datasets(self) -> list[str]:
        """Return names of all existing datasets/projects in this sink."""
        ...

    @abstractmethod
    async def create_dataset(self, name: str) -> None:
        """Create a named dataset. No-op if it already exists."""
        ...

    @abstractmethod
    async def post_file(
        self,
        dataset: str,
        record: dict,
        filename: str | None = None,
    ) -> None:
        """Write one record to the dataset. filename hint used by ZIP sink."""
        ...

    @abstractmethod
    async def finalise(self) -> None:
        """Flush buffers and close the sink (e.g. seal ZIP archive)."""
        ...
```

### 7.2 Sink Registry

```python
# src/databridge/sinks/__init__.py

_SINK_REGISTRY: dict[str, type[BaseSink]] = {
    "dataset-mock":    DatasetMockSink,
    "annotator-mock":  AnnotatorMockSink,
    "local-zip":       LocalZipSink,
    "local-jsonl":     LocalJsonlSink,
}

def get_sink(config: DatasinkConfig) -> BaseSink:
    """Single dispatch — registry lookup, no type-branch logic."""
    cls = _SINK_REGISTRY.get(config.type)
    if cls is None:
        raise ValueError(f"Unknown datasink type: {config.type!r}")
    return cls(config)
```

### 7.3 Concrete Sinks

**DatasetMockSink** (`dataset-mock`):
- `ping()` → `GET {url}/health`
- `list_datasets()` → `GET {url}/datasets` → `data["datasets"]`
- `create_dataset(name)` → `POST {url}/datasets` with `{"name": name}`; ignore 409 (already exists)
- `post_file(dataset, record, filename)` → `POST {url}/datasets/{dataset}/files` with record JSON
- `finalise()` → no-op (HTTP sink; no buffer)

**AnnotatorMockSink** (`annotator-mock`) — adapter layer:
- `ping()` → `GET {url}/health`
- `list_datasets()` → `GET {url}/api/v1/projects` → extract `name` fields
- `create_dataset(name)` → `POST {url}/api/v1/projects` with `{"name": name}`; ignore 409
- `post_file(dataset, record, filename)` → `POST {url}/api/v1/projects/{dataset}/tasks` with record
- `finalise()` → no-op

**LocalZipSink** (`local-zip`):
- `ping()` → check `config.path` directory exists and is writable
- `list_datasets()` → return existing ZIP filenames (without extension) in `config.path`
- `create_dataset(name)` → open `zipfile.ZipFile(f"{config.path}/{name}_{job_id}.zip", "w")` in memory buffer; store reference
- `post_file(dataset, record, filename)` → resolve filename template with record fields; fall back to `sha256(record_json)[:16]`; write JSON string as file inside ZIP
- `finalise()` → close and flush ZIP archive; if disk full → raise `OSError` (job handler marks job failed, attempts cleanup)

**LocalJsonlSink** (`local-jsonl`):
- `ping()` → check `config.path` directory exists and is writable
- `list_datasets()` → return existing JSONL filenames (without extension) in `config.path`
- `create_dataset(name)` → open file `{config.path}/{name}_{job_id}.jsonl` for writing
- `post_file(dataset, record, filename)` → try `json.dumps(record)` + newline; if not serializable → skip (increment `records_skipped`); do not raise
- `finalise()` → flush and close file

---

## 8. Worker Task (`src/databridge/export/worker.py`)

```python
async def run_export_job(ctx: dict, job_id: str) -> None:
    """ARQ worker task. ctx contains db pool + settings."""
    # 1. Load job from DB; if not found → no-op (deleted by TTL sweep)
    # 2. Update status → running, started_at = NOW(), last_heartbeat_at = NOW()
    # 3. get_adapter(datasource) → adapter (assert ExportableAdapter)
    # 4. count = await adapter.count(filter)  → update records_total in DB
    # 5. get_sink(datasink_config) → sink; await sink.ping()
    # 6. await sink.create_dataset(destination_dataset)
    # 7. if asset_resolution: await sink.create_dataset(asset_dataset)  [asset_sink if configured]
    # 8. for offset in range(0, count, batch_size):
    #      records = await adapter.fetch_page(..., limit=batch_size, offset=offset)
    #      for record in records:
    #        if asset_resolution: record = await resolve_assets(record, job, asset_sink)
    #        filename = resolve_filename_template(config.filename_template, record)
    #        await sink.post_file(destination_dataset, record, filename)
    #      await update_progress(db, job_id, processed, skipped, asset_errors)
    #      emit keep-alive if batch took > keepalive_interval_minutes
    # 9. await sink.finalise()
    # 10. update status → completed, completed_at = NOW()
```

**Error handling**:
- Sink unreachable at start → status=`failed`, error_message set, no records written
- Asset fetch failure for a record → skip record, increment `asset_errors`, continue
- Disk full (local sinks) → status=`failed`, attempt ZIP cleanup
- Unhandled exception → caught by ARQ, status=`failed`, exception message logged + stored

---

## 9. Source Code Structure

```text
src/databridge/
├── config.py                    # + DatasinkConfig, ExportSettings, Settings extended
├── auth.py                      # extended: org_id, user_id, role fields in AuthContext
├── metrics.py / export_metrics.py  # + 8 new export Prometheus instruments
│
├── sinks/
│   ├── __init__.py              # get_sink() factory + _SINK_REGISTRY
│   ├── base.py                  # BaseSink ABC
│   ├── dataset_mock.py          # DatasetMockSink
│   ├── annotator_mock.py        # AnnotatorMockSink
│   └── local_zip.py + local_jsonl.py
│
├── export/
│   ├── __init__.py
│   ├── models.py                # FilterSnapshot, ExportJob, ExportJobCreate, ExportJobResponse, ...
│   ├── db.py                    # insert/select/update queries for export_jobs
│   ├── worker.py                # ARQ task: run_export_job() + WorkerSettings
│   ├── sweep.py                 # stale job sweep: mark_stale_jobs(), ttl_purge_jobs()
│   └── asset.py                 # detect_asset_url_fields(), resolve_assets()
│
├── routes/
│   ├── datasinks.py             # GET /api/v1/datasinks, GET /api/v1/datasinks/{name}/datasets
│   │                            # POST /api/v1/datasinks/{name}/detect-asset-fields
│   └── export_jobs.py           # POST/GET /api/v1/export-jobs, GET/POST(retry) /:id
│
└── adapters.py                  # + ExportableAdapter protocol, count()/fetch_page() on existing adapters

worker/
└── __main__.py                  # python -m worker  →  arq worker entrypoint

tests/
├── unit/
│   ├── test_sinks.py
│   ├── test_export_worker.py
│   └── test_asset.py
├── integration/
│   ├── test_export_jobs.py
│   └── test_datasinks.py
└── e2e/
    └── test_export_flow.py
```

---

## 10. Prometheus Metrics

```python
# src/databridge/export_metrics.py

from prometheus_client import Counter, Gauge

EXPORT_JOBS_CREATED = Counter(
    "export_jobs_created_total",
    "Export jobs created",
    ["org_id", "sink_type"],
)
EXPORT_JOBS_COMPLETED = Counter(
    "export_jobs_completed_total",
    "Export jobs completed successfully",
    ["org_id", "sink_type"],
)
EXPORT_JOBS_FAILED = Counter(
    "export_jobs_failed_total",
    "Export jobs failed",
    ["org_id", "sink_type"],
)
EXPORT_ACTIVE_JOBS = Gauge(
    "export_active_jobs",
    "Currently active (running + pending) export jobs",
    ["org_id"],
)
EXPORT_RECORDS_PER_SECOND = Gauge(
    "export_records_per_second",
    "Records exported per second (updated per batch)",
    ["sink_type"],
)
EXPORT_ASSET_RESOLUTION_SUCCESS = Counter(
    "export_asset_resolution_success_total",
    "Assets successfully fetched and stored",
)
EXPORT_ASSET_RESOLUTION_FAILED = Counter(
    "export_asset_resolution_failed_total",
    "Asset fetches that failed (record skipped)",
)
EXPORT_ORG_CONCURRENT_JOBS = Gauge(
    "export_org_concurrent_jobs",
    "Concurrent export jobs per org (running + pending)",
    ["org_id"],
)
```

---

## 11. UI `data-testid` Map

All interactive export UI elements and dynamically rendered content must carry stable `data-testid` attributes.

| `data-testid` | Element | When visible |
|---|---|---|
| `export-block` | Export & Destination section container | Always (when datasource selected) |
| `datasink-select` | Datasink dropdown | Always in export block |
| `destination-dataset-select` | Dataset name combobox (select + free text) | Always in export block |
| `asset-resolution-toggle` | Toggle switch | Always in export block |
| `asset-datasink-select` | Asset datasink dropdown | When toggle ON |
| `asset-dataset-label` | Read-only derived asset dataset name | When toggle ON |
| `asset-url-fields-list` | Container of field checkboxes | When toggle ON |
| `asset-url-field-{name}` | Individual field checkbox (name = field path, dots → dashes) | When toggle ON |
| `asset-url-prefix-input` | Asset URL prefix text input | When toggle ON |
| `export-btn` | Export submit button | Always in export block |
| `jobs-tab` | Jobs navigation tab button | Always visible in main nav |
| `jobs-list` | Jobs list container | On Jobs tab |
| `job-row-{id}` | Individual job row | Per job in list |
| `job-status-{id}` | Status badge | Per job row |
| `job-progress-{id}` | Progress display ("12 exported" or "12 / 100 (12%)") | Per job row |
| `job-source-{id}` | Source name label | Per job row |
| `job-sink-{id}` | Sink name label | Per job row |
| `job-retry-btn-{id}` | Retry button | Per failed job row |
| `job-download-btn-{id}` | Download link | Per completed local-sink job row |

**Playwright convention**: all selectors use `getByTestId(…)` or `[data-testid="…"]` — never CSS class or DOM index.

---

## 12. Sequence Diagram — Export Job Lifecycle

```plantuml
@startuml
participant UI
participant API as "FastAPI\n(databridge)"
participant DB as "PostgreSQL\n(export_jobs)"
participant Redis
participant Worker as "ARQ Worker"
participant Sink

UI -> API : POST /api/v1/export-jobs
API -> DB  : INSERT export_job (pending)
API -> Redis : enqueue run_export_job(job_id)
API -> UI  : 201 ExportJobResponse

loop poll every 3 s
  UI -> API : GET /api/v1/export-jobs/{id}
  API -> DB  : SELECT status, progress
  API -> UI  : ExportJobResponse
end

Worker -> Redis : dequeue job
Worker -> DB   : UPDATE status=running, started_at
Worker -> Sink : ping()
Worker -> Sink : create_dataset()
loop batch
  Worker -> Datasource : fetch_page()
  Worker -> Sink : post_file() * N
  Worker -> DB   : UPDATE records_processed, last_heartbeat_at
end
Worker -> Sink : finalise()
Worker -> DB   : UPDATE status=completed, completed_at
@enduml
```
