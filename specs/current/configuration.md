# Source Configuration

## One-liner
YAML config file loaded at startup with vault sidecar and env-var secret injection, defining system datasources (S3, ClickHouse, Trino, Langfuse), user-facing connection templates, dataset upload targets, and server tuning.

## Problem
Services need to talk to multiple backends without hardcoding credentials. Ops teams inject secrets via a Vault sidecar or environment variables. Config must be validated strictly at startup — unknown keys should fail loudly, not silently degrade.

---

## 1. Config File Location

Resolved in priority order at module import time:

1. `DATAIMPORTER_CONFIG` environment variable (absolute path).
2. `config.yaml` two directories above `config.py` (i.e., next to the project root — used during local development).
3. `config.yaml` in the current working directory (production default).

```python
def _find_config() -> Path:
    if env := os.environ.get("DATAIMPORTER_CONFIG"):
        return Path(env)
    src_relative = Path(__file__).resolve().parents[2] / "config.yaml"
    if src_relative.exists():
        return src_relative
    return Path("config.yaml")
```

`get_settings()` is `@lru_cache` — the file is read once at first call and held for the lifetime of the process. To reload without restarting, call `get_settings.cache_clear()`.

---

## 2. Secret Injection

Secrets never belong in config.yaml in plain text. Two injection mechanisms are supported, applied in order before YAML parsing:

### Vault sidecar file

Path: `VAULT_SECRETS_PATH` env var, default `/vault/secrets/env`.

Supported formats (auto-detected per line):
```
KEY=value
export KEY=value
KEY: value
```

References in the YAML use `vault:KEY` syntax:
```yaml
access_key_id: "vault:S3_ACCESS_KEY_ID"
secret_access_key: "vault:S3_SECRET_ACCESS_KEY"
```

### Environment variable expansion

After vault substitution, `os.path.expandvars` is applied — any `$VAR` or `${VAR}` in the YAML is replaced with the process environment. This is the simpler path for local dev or k8s secret env-vars.

```yaml
password: "$CLICKHOUSE_PASSWORD"
```

Both mechanisms can be mixed in the same file.

---

## 3. Config Structure

```yaml
datasources:   # system backends — credentials held by the service
  - ...

connections:   # allowlisted templates — users supply their own credentials
  - ...

targets:       # dataset upload destinations
  - ...

server:        # HTTP server tuning
  ...
```

---

## 4. `datasources` — System Datasources

System datasources are backend connections that the service owns and manages. Credentials are injected at startup; they are never exposed to end-users.

Each datasource has a `name` (used as an identifier in API calls) and a `type`.

### `type: s3`

Reads raw event files from an S3-compatible object store (AWS S3, MinIO, Ceph).

```yaml
datasources:
  - name: "prod-s3"
    type: s3
    bucket: "llogr-raw-events"
    region: "us-east-1"          # default: us-east-1
    endpoint: "http://minio:9000" # omit for AWS
    access_key_id: "vault:S3_KEY"
    secret_access_key: "vault:S3_SECRET"
    public_endpoint: ""           # optional: rewrite presigned URL host for browser downloads
    key_prefix: ""                # optional: scope all operations under a path prefix
    addressing_style: "virtual"   # "virtual" (AWS default) or "path" (MinIO/Ceph)
    presign_expiry: 3600          # presigned URL TTL in seconds
```

Full-text search over S3 files is done in-process via DuckDB (`duckdb_temp_dir` controls its temp directory, default `/tmp/duckdb_temp`).

### `type: clickhouse`

Reads from a ClickHouse HTTP interface. Used for structured log search.

```yaml
  - name: "prod-ch"
    type: clickhouse
    url: "http://clickhouse:8123"
    database: "default"
    table: "llogr_events"        # default: llogr_events
    user: "default"
    password: "vault:CH_PASSWORD"
```

Credentials are passed as query parameters on every request. `httpx`/`httpcore` loggers are silenced to WARNING to prevent password leaking into access logs.

### `type: chyt`

ClickHouse-over-YT (CHYT) — same HTTP interface as ClickHouse but routed through a YT proxy. Uses the same fields as `clickhouse`.

### `type: trino`

Reads from a Trino (formerly PrestoSQL) cluster via the `/v1/statement` REST API with async polling.

```yaml
  - name: "prod-trino"
    type: trino
    url: "http://trino:8080"
    user: "trino"
    catalog: "hive"
    schema_name: "events"
```

Authentication is via `X-Trino-User` header. No password field — Trino auth is typically handled at the cluster level or via a separate auth proxy.

### `type: langfuse`

Reads traces and observations from a Langfuse instance via its REST API.

```yaml
  - name: "prod-langfuse"
    type: langfuse
    url: "http://langfuse:3000"
    access_key_id: "vault:LF_PUBLIC_KEY"    # Langfuse public key → HTTP Basic username
    secret_access_key: "vault:LF_SECRET_KEY" # Langfuse secret key → HTTP Basic password
```

Auth is HTTP Basic (`access_key_id:secret_access_key`). `httpx`/`httpcore` loggers silenced for the same reason as ClickHouse.

---

## 5. `connections` — User-Facing Connection Templates

Connections are allowlisted endpoint/type pairs that end-users can authenticate against using their own credentials. The service never stores user credentials — they are passed per-request.

The `connections` list controls what appears in the UI. It does not grant access; it defines which backends users are permitted to target.

```yaml
connections:
  - type: langfuse
    url: "http://langfuse:3000"
    label: "Production Langfuse"    # display name in the UI
    public_key: ""                  # optional pre-filled default
    secret_key: ""

  - type: s3
    url: "http://minio:9000"
    label: "Internal MinIO"
    bucket: "llogr-raw-events"
    region: "us-east-1"
    addressing_style: "path"
    key_prefix: ""
```

`has_credentials: true` is surfaced to the UI when `public_key` and `secret_key` are both non-empty (i.e., the operator has pre-filled them so users don't need to enter their own).

---

## 6. `targets` — Dataset Upload Targets

Targets define where imported datasets are sent. Each target is a remote dataset service with OAuth2 client credentials.

```yaml
targets:
  - name: "prod"
    base_url: "https://dataset-service.internal"
    token_url: "https://auth.internal/oauth2/token"
    client_id: "vault:DS_CLIENT_ID"
    client_secret: "vault:DS_CLIENT_SECRET"
    default_access: "organization"   # default access level for created datasets
    default_dataset_type: "DATASET"
    upload_timeout: 300              # seconds; covers ~100 MB at ~3 MB/s
```

Leave `token_url` empty to disable OAuth2 (used for mock/dev dataset services that ignore `Authorization` headers).

---

## 7. `server` — HTTP Server Tuning

```yaml
server:
  host: "0.0.0.0"
  port: 5001
  workers: 1
  root_path: ""                  # ASGI root_path for reverse-proxy prefix stripping
  timeout_keep_alive: 65
  timeout_worker_healthcheck: 30
  debug: false
  silence_probes: true           # suppress /livez /ready /health /metrics from access logs
  hide_auth_inputs: false        # hide credential fields in the UI (operator-managed auth)
  redis_url: ""                  # required for async job queue (arq); empty = inline mode
  worker_metrics_port: 9101      # Prometheus port for the arq worker process
```

---

## 8. Validation

All four sections are validated at load time:

- **Unknown keys** raise `ValueError` immediately with a list of the offending keys and the valid alternatives. This prevents silent misconfiguration from typos.
- **Type mismatches** (e.g. a string where an int is expected) raise `TypeError` from the dataclass constructor.
- The `Settings` object and all nested dataclasses are `frozen=True` — mutation after startup is not possible.

```python
# Raises: ValueError: Unknown key(s) in datasource 'prod-s3': ['buket'].
#         Check for typos — valid keys: ['access_key_id', 'addressing_style', 'bucket', ...]
```

---

## 9. Adapter Protocol

Each datasource type maps to a concrete adapter class via a registry. All adapters implement the same `DatasourceAdapter` protocol:

```python
class DatasourceAdapter(Protocol):
    async def search(self, query: str, *, auth: AuthContext, ...) -> list[dict]: ...
    async def ping(self) -> None: ...
```

`get_adapter(ds)` looks up the adapter by `ds.type` and raises `ValueError` for unknown types. The health probe endpoints call `ping()` on all configured system datasources.

| `type` | Adapter class | `ping()` method |
|---|---|---|
| `s3` | `S3Adapter` | `head_bucket` |
| `clickhouse` | `ClickhouseAdapter` | `GET /ping` |
| `chyt` | `ChytAdapter` | `GET /ping` |
| `trino` | `TrinoAdapter` | `GET /v1/info` |
| `langfuse` | `LangfuseAdapter` | `GET /api/public/health` |

---

## 10. Scope

**In:** YAML config file; vault sidecar injection; env-var expansion; all four config sections (`datasources`, `connections`, `targets`, `server`); strict unknown-key validation; adapter registry.

**Out:** Runtime config reload without restart; per-user datasource registration; config schema versioning; encrypted config files.
