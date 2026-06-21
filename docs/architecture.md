# Databridge Architecture

## Overview

Databridge is a data pipeline service for browsing, previewing, and exporting data from heterogeneous sources into configurable sinks. It runs as two cooperative processes: a **FastAPI web server** and an **ARQ background worker**, sharing a PostgreSQL database and a Redis queue.

```
                     ┌──────────────────────────────────┐
                     │          FastAPI Server           │
  Browser / API ───► │  /api/v1/*   /   /metrics        │
                     └──────┬──────────────┬────────────┘
                            │              │
                         asyncpg        arq.Pool
                            │              │
                     ┌──────▼──────┐  ┌───▼──────┐
                     │ PostgreSQL  │  │  Redis   │
                     └──────▲──────┘  └───┬──────┘
                            │             │ dequeue
                     ┌──────┴──────────────▼──────┐
                     │         ARQ Worker          │
                     │   worker/__main__.py        │
                     └─────────────────────────────┘
```

---

## Process Map

| Process | Entry point | Role |
|---|---|---|
| Web server | `src/databridge/main.py` → `create_app()` | REST API + browser UI |
| Export worker | `worker/__main__.py` | Pulls export jobs from Redis, runs them |

Both processes share the same `databridge` Python package and read the same `config.yaml`.

---

## Configuration

File: `config.yaml` (location resolved via `DATABRIDGE_CONFIG` env var, then project root, then cwd).

```yaml
server:        # host, port, workers, debug, public_url, hide_auth_inputs
database_url:  # PostgreSQL DSN
encryption_key: # Fernet key for credential encryption
datasources:   # list of system-level sources (S3, ClickHouse, Trino, …)
datasinks:     # list of export sinks (dataset-mock, annotator-mock, local-zip, local-jsonl)
export:        # batch_size, redis_url, webhook_allowed_url_prefixes, timeouts
```

Secret values can be injected as `vault:<KEY>` (resolved from a sidecar file at `VAULT_SECRETS_PATH`) or as `$ENV_VAR` references. The loader (`config.py:get_settings`) is `@lru_cache`-memoised — config is read once per process.

---

## Authentication

`auth.py` extracts identity from each HTTP request (no sessions, no tokens managed by this service):

1. **`X-Group-ID` header** (primary) — set by an upstream nginx proxy. Format: `org_id/user_id`. `X-Role` header maps to `super_admin | org_admin | user`.
2. **HTTP Basic auth** — username is treated as `org_id/user_id`.
3. **Debug fallback** — when `server.debug = true`, unauthenticated requests get `dev/dev` identity with org-admin rights.

The resolved `AuthContext` (org_id, user_id, role, is_org_admin) is injected into route handlers via FastAPI's `Depends(get_auth)`.

---

## Database Layer

PostgreSQL via `asyncpg`. A single connection pool (`db/pool.py`) is created on startup and attached to `app.state.pool`. Routes receive it via `Depends(get_pool)`.

Schema is managed with **Alembic** migrations (`db/migrations/versions/`).

### Core tables

**`connections`** — user-managed datasource connections. Credentials are stored encrypted (Fernet, `crypto.py`). Scoped by `owner_key` = `public_key` from auth context.

| Column | Description |
|---|---|
| `id` | UUID PK |
| `owner_key` | `org_id/user_id` — access scope |
| `type` | `s3 \| clickhouse \| trino \| langfuse \| dataset` |
| `role` | `source \| sink` |
| `credentials_enc` | Fernet-encrypted JSON |
| `status` | `untested \| reachable \| unreachable` |

**`export_jobs`** — export job state machine.

| Column | Description |
|---|---|
| `id` | UUID PK |
| `org_id / user_id` | Owner |
| `datasource_type` | `connection \| system` |
| `datasource_ref` | Connection UUID or system source name |
| `datasource_filter` | JSONB: `{query, start, end, time_field, limit}` |
| `datasink_name` | References a name in `config.datasinks` |
| `destination_dataset` | Dataset/folder name inside the sink |
| `status` | `pending → running → completed \| failed \| cancelled` |
| `masking_rules` | JSONB array of field masking rules |
| `sampling_config` | JSONB: method, ratio, max_items |
| `webhook_url / webhook_enabled / webhook_payload_template` | Completion webhook |
| `asset_*` | Asset resolution config (see below) |

---

## Source Adapters (`adapters.py`)

The `ConnectionAdapter` and `ExportableAdapter` protocols define the interface. `BaseAdapter` provides shared URL/credential plumbing.

Concrete adapters:

| Adapter class | Datasource type | Transport |
|---|---|---|
| `ClickHouseAdapter` | `clickhouse` | HTTP JSON API |
| `TrinoAdapter` | `trino` | Trino REST API |
| `LangfuseAdapter` | `langfuse` | Langfuse HTTP API |
| `S3DuckDBAdapter` | `s3` | DuckDB with httpfs extension (reads Parquet/JSON from S3) |
| `DatasetAdapter` | `dataset` | HTTP API (mock dataset service) |

All adapters implement:
- `ping()` — connectivity check
- `preview(query, start, end, limit)` — return sample rows
- `schema(start, end)` — infer field schema from a sample
- `count(query, start, end)` — total row count for export sizing
- `fetch_page(query, start, end, limit, offset)` — paginated row fetch

`get_adapter(conn_or_config, creds)` selects the right adapter class from a registry keyed on `type`.

---

## Export Pipeline

### Job lifecycle

```
POST /api/v1/export-jobs
  └─► insert row (status=pending)
  └─► enqueue job_id into Redis via ARQ
        └─► ARQ worker dequeues
              └─► run_export_job(ctx, job_id)
                    ├─ load adapter (connection or system source)
                    ├─ count total records
                    ├─ create dataset in sink
                    ├─ batch loop: fetch → filter → mask → resolve assets → post_file
                    ├─ update status = completed / failed / cancelled
                    └─ fire webhook (async task)
```

### Sampling (`export/sampling.py`)

`SamplingBuffer` implements three strategies before records are written to the sink:

- **random** — probabilistic keep by ratio, or first-N absolute count
- **systematic** — every-Nth record
- **stratified** — per-group quota or ratio, keyed on a `target_column`

### Masking (`export/masking.py`)

Per-field rules applied to every record before writing. Actions: `mask` (→ `"***"`), `hash` (SHA-256), `drop` (remove field), `redact` (→ `"[REDACTED]"`). Nested fields addressed via dot-paths.

### Asset Resolution (`export/asset.py`)

Optional per-job step: for each record, URL-valued fields (detected by name pattern or content) are fetched via HTTP and uploaded to a separate asset sink/dataset. The original URL in the record is replaced with the stored reference.

### Webhook (`export/webhook.py`)

Fired asynchronously on job completion or failure. Payload is a JSON template with `{{variable}}` placeholders (`job_id`, `status`, `records_processed`, `download_url`, etc.).

---

## Sink Implementations (`sinks/`)

`BaseSink` (ABC) interface: `ping()`, `list_datasets()`, `create_dataset()`, `post_file()`, `finalise()`.

| Class | Config type | Behaviour |
|---|---|---|
| `LocalZipSink` | `local-zip` | Accumulates files in-memory, writes a `.zip` at `finalise()` to a local path |
| `LocalJsonlSink` | `local-jsonl` | Appends each record as JSONL to a local file |
| `DatasetMockSink` | `dataset-mock` | POSTs multipart files to a mock Dataset service REST API |
| `AnnotatorMockSink` | `annotator-mock` | Uploads tasks to a mock Annotation service REST API |

Local sinks expose a download endpoint (`GET /api/v1/export-jobs/{id}/download`) that serves the generated file.

---

## API Routes

All routes are prefixed `/api/v1` except the UI and health endpoints.

| Router | Prefix / path | Responsibility |
|---|---|---|
| `routes/health.py` | `/healthz`, `/readyz` | Liveness + readiness probes |
| `routes/connections.py` | `/api/v1/connections` | CRUD for user connections; ping, preview, schema, PII detection, asset field detection |
| `routes/datasinks.py` | `/api/v1/datasinks` | List configured sinks; list/create datasets within a sink |
| `routes/export_jobs.py` | `/api/v1/export-jobs` | Create, list, get, cancel export jobs; download local output |
| `routes/ui.py` | `/`, `/api/v1/ui-config` | Serves the browser SPA + JS/CSS with cache-busting hashes |
| `main.py` | `/metrics` | Prometheus metrics endpoint |

---

## Browser UI

A vanilla-JS single-page application served from `src/databridge/static/` (JS + CSS) and `src/databridge/templates/browser.html`.

The UI covers:
- Datasource browser (list connections + system sources, preview data, inspect schema)
- Export job creation wizard (filter → sink → masking → sampling → asset resolution → webhook)
- Export job list with status polling

---

## Observability

- **Structured logging** — `structlog` with JSON output in production, pretty in debug mode. Request IDs propagated via `x-request-id` header (middleware in `main.py`).
- **Prometheus metrics** — `metrics.py` (request duration/count via `PrometheusMiddleware`) and `export_metrics.py` (job counters, records/sec, asset errors, webhook delivery, sampling drops).

---

## Dev Infrastructure (`docker-compose.dev.yml`)

| Service | Port | Purpose |
|---|---|---|
| `postgres` | 5432 | Primary database |
| `clickhouse` | 8123 / 9000 | ClickHouse source for integration tests |
| `redis` | 6379 | ARQ job queue |
| `minio` | 9200 (S3 API) / 9201 (console) | Local S3-compatible store |
| `dataset-mock` | 9101 | Mock Dataset sink service |
| `annotator-mock` | 8011 | Mock Annotation sink service |

---

## Key Dependencies

| Package | Role |
|---|---|
| `fastapi` + `uvicorn` | Web framework |
| `asyncpg` | Async PostgreSQL driver |
| `alembic` | Database migrations |
| `arq` + `redis` | Async job queue (worker tasks) |
| `cryptography` | Fernet encryption for stored credentials |
| `duckdb` + `duckdb-extension-httpfs` | S3 adapter query engine |
| `httpx` | Async HTTP client (adapters, asset fetch, webhooks) |
| `structlog` | Structured logging |
| `prometheus-client` | Metrics exposition |
| `pyyaml` | Config file parsing |
