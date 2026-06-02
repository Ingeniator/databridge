# Quickstart: Datasink Export Pipeline (Phase 2)

This extends the Phase 1 local dev setup (`001-datasource-connection-sync/quickstart.md`) with the export pipeline additions: Redis, the ARQ worker, and datasink configuration.

---

## Prerequisites

Everything from Phase 1, plus:
- Redis running (via docker-compose below)
- `arq>=0.26` and `redis[hiredis]>=5.0` installed (added to `pyproject.toml` in Phase 2)

---

## 1. Start dependencies

From the `ai-suite` root:

```bash
docker compose up -d postgres redis annotator-mock dataset-mock
```

| Service | Port |
|---------|------|
| postgres | 5432 |
| redis | 6379 |
| annotator-mock | 8010 |
| dataset-mock | 8020 |

---

## 2. Install updated dependencies

```bash
uv sync
```

---

## 3. Configure the service

Extend `config.yaml` with the new `datasinks` and `export` sections:

```yaml
server:
  host: "0.0.0.0"
  port: 5010
  debug: true
  silence_probes: false

database_url: "postgresql://postgres:postgres@localhost:5432/databridge"
encryption_key: "${DATABRIDGE_ENCRYPTION_KEY}"

datasources: []

# ── Datasinks (admin-configured, global) ────────────────────────────────────
datasinks:
  - name: "local-annotator-mock"
    type: annotator-mock
    url: "http://localhost:8010"

  - name: "local-dataset-mock"
    type: dataset-mock
    url: "http://localhost:8020"

  - name: "local-exports-zip"
    type: local-zip
    path: "/tmp/databridge-exports"
    filename_template: "{id}_{timestamp}.json"

  - name: "local-exports-jsonl"
    type: local-jsonl
    path: "/tmp/databridge-exports"

# ── Export pipeline tunables ────────────────────────────────────────────────
export:
  stale_job_timeout_minutes: 15
  max_concurrent_jobs_per_org: 5
  job_ttl_days: 7
  poll_interval_seconds: 3
  keepalive_interval_minutes: 2
  batch_size: 100
  redis_url: "redis://localhost:6379"
```

Create the local export output directory:

```bash
mkdir -p /tmp/databridge-exports
```

---

## 4. Run database migrations

```bash
uv run alembic upgrade head
```

This applies the new `export_jobs` table migration.

---

## 5. Start the API service

```bash
uv run uvicorn databridge.main:app --host 0.0.0.0 --port 5010 --reload
```

UI: http://localhost:5010  
API docs: http://localhost:5010/docs

---

## 6. Start the ARQ worker

In a separate terminal:

```bash
uv run python -m worker
```

The worker polls Redis for export jobs and processes them in the background.

In `debug` mode (when `server.debug=true`), the service also accepts a synchronous in-process fallback for development — but the ARQ worker is recommended to test the full pipeline.

---

## 7. Run tests

```bash
# Unit tests (no external services needed)
uv run pytest tests/unit -v

# Integration tests (requires postgres + redis running)
uv run pytest tests/integration -v

# E2E tests (requires all services + API running on :5010)
uv run pytest tests/e2e -v
```

---

## 8. Create and monitor an export job (curl)

```bash
# List configured datasinks
curl -s http://localhost:5010/api/v1/datasinks \
  -H 'X-Group-ID: acme/alice' -H 'X-Role: USER' | jq .

# List available datasets in a datasink
curl -s http://localhost:5010/api/v1/datasinks/local-annotator-mock/datasets \
  -H 'X-Group-ID: acme/alice' -H 'X-Role: USER' | jq .

# Create an export job (export from a system source to annotator-mock)
curl -s -X POST http://localhost:5010/api/v1/export-jobs \
  -H 'Content-Type: application/json' \
  -H 'X-Group-ID: acme/alice' -H 'X-Role: USER' \
  -d '{
    "datasource_type": "system",
    "datasource_ref": "prod-clickhouse",
    "datasource_filter": {"query": "", "start": null, "end": null},
    "datasink_name": "local-annotator-mock",
    "destination_dataset": "test-export",
    "asset_resolution": false
  }' | jq .

# Poll job status (replace {id} with the returned job ID)
curl -s http://localhost:5010/api/v1/export-jobs/{id} \
  -H 'X-Group-ID: acme/alice' -H 'X-Role: USER' | jq '{status, records_processed, records_total}'

# List all your jobs
curl -s http://localhost:5010/api/v1/export-jobs \
  -H 'X-Group-ID: acme/alice' -H 'X-Role: USER' | jq .

# Retry a failed job
curl -s -X POST http://localhost:5010/api/v1/export-jobs/{id}/retry \
  -H 'X-Group-ID: acme/alice' -H 'X-Role: USER' | jq .
```

---

## 9. Auth headers reference

| Header | Format | Example |
|--------|--------|---------|
| `X-Group-ID` | `org_id/user_id` | `acme/alice` |
| `X-Role` | `USER` \| `ORG_ADMIN` \| `SUPER_ADMIN` | `ORG_ADMIN` |

In `debug` mode (no auth headers supplied), the service falls back to `org_id=dev`, `user_id=dev`, `role=super_admin`.

---

## 10. Env vars reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABRIDGE_CONFIG` | `config.yaml` | Config file path |
| `VAULT_SECRETS_PATH` | `/vault/secrets/env` | Vault sidecar file |
| `PROMETHEUS_MULTIPROC_DIR` | unset | Enable multi-process Prometheus metrics (required when running API + worker together) |
