# databridge

Connection management service for AI data pipelines. Create, test, and browse data from named connections (S3, ClickHouse, Trino, Langfuse, dataset sinks) with credentials encrypted at rest. Administrator-configured system sources defined in `config.yaml` are served read-only alongside user connections. A vanilla-JS SPA is served from the same process.

See [quickstart](specs/changes/001-datasource-connection-sync/quickstart.md) for local dev setup.

## Supported connection types

| Type | Role | Description |
|------|------|-------------|
| `s3` | source | List and scan files in an S3/MinIO bucket via DuckDB |
| `clickhouse` | source | Query via the ClickHouse HTTP interface |
| `trino` | source | SQL via the Trino REST API (polling-based) |
| `langfuse` | source | Fetch traces via the Langfuse REST API |
| `dataset` | sink | Upload records to a dataset service |

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/livez` | Liveness probe |
| GET | `/ready` | Readiness probe (per-component) |
| GET | `/api/v1/health` | Detailed health + version |
| GET | `/api/v1/ui-config` | SPA bootstrap config |
| GET | `/api/v1/connections` | List all connections (user + system sources) |
| POST | `/api/v1/connections` | Create a connection |
| POST | `/api/v1/connections/test` | Test credentials without saving |
| GET | `/api/v1/connections/{id}` | Get a connection |
| PATCH | `/api/v1/connections/{id}` | Update label or credentials |
| DELETE | `/api/v1/connections/{id}` | Delete a connection |
| POST | `/api/v1/connections/{id}/ping` | Test reachability |
| POST | `/api/v1/connections/{id}/preview` | Sample data (source only) |
| GET | `/api/v1/connections/{id}/schema` | Discover field schema (source only) |
| GET | `/` | Browser SPA |
| GET | `/metrics` | Prometheus metrics |

Full OpenAPI spec: [contracts/openapi.yaml](specs/changes/001-datasource-connection-sync/contracts/openapi.yaml)

## Quick start

```bash
cp config.yaml.example config.yaml
# Edit config.yaml — set database_url and encryption_key
uv run alembic upgrade head
uv run uvicorn databridge.main:app --port 5010 --reload
```

Browser UI: http://localhost:5010  
API docs: http://localhost:5010/docs

## Demo mode

Demo mode runs the service without PostgreSQL or Redis. All data is stored in-memory and lost on restart. No authentication headers are required — every request is served as a super-admin.

```bash
DATABRIDGE_CONFIG=config.demo.yaml uv run uvicorn databridge.main:app --port 5010
```

The bundled `config.demo.yaml` starts with a single `local-zip` sink writing to `/tmp/databridge-demo`. To customise it, copy and edit:

```bash
cp config.demo.yaml config.local-demo.yaml
# Edit datasources / datasinks as needed
DATABRIDGE_CONFIG=config.local-demo.yaml uv run uvicorn databridge.main:app --port 5010
```

To enable demo mode in your own config file, set the top-level flag:

```yaml
demo: true
```

When `demo: true`:
- No database connection is opened (`database_url` is ignored)
- No Redis connection is attempted (`export.redis_url` is ignored)
- Export jobs are accepted and queued in-memory but no worker processes them — jobs remain `pending`
- All requests are authenticated as `demo/demo` (org `demo`, role `super_admin`) when no auth header is present

## Running tests

```bash
uv run pytest tests/unit tests/integration -v
```

E2E tests (require running service):
```bash
uv run pytest tests/e2e -v
```
