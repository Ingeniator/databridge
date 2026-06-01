# Data Model: Datasource Connection Management

**Phase 1 output** | **Date**: 2026-06-01

---

## 1. Domain Entities

### 1.0 Configuration Dataclasses (loaded from `config.yaml`)

```python
@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 5010
    workers: int = 1
    root_path: str = ""
    debug: bool = False
    silence_probes: bool = True
    hide_auth_inputs: bool = False

@dataclass(frozen=True)
class SystemSourceConfig:
    """One entry under `datasources:` in config.yaml. Credentials resolved at startup."""
    name: str
    type: str           # "s3"|"clickhouse"|"trino"|"langfuse"
    # resolved fields — type-specific, vault/env secrets already substituted
    url: str = ""
    bucket: str = ""
    region: str = "us-east-1"
    access_key_id: str = ""
    secret_access_key: str = ""
    key_prefix: str = ""
    database: str = "default"
    table: str = "llogr_events"
    user: str = ""
    password: str = ""
    catalog: str = ""
    schema_name: str = ""

    @property
    def id(self) -> UUID:
        """Deterministic UUID v5 of the source name. Rename = new ID."""
        import uuid
        return uuid.uuid5(uuid.NAMESPACE_DNS, self.name)

@dataclass(frozen=True)
class Settings:
    server: ServerConfig
    database_url: str          # e.g. "postgresql://user:pass@host/db"
    encryption_key: str        # Fernet key (base64-urlsafe, 32 bytes)
    datasources: tuple[SystemSourceConfig, ...]  # system sources — empty tuple if none
```

**Config YAML shape** (with vault/env references, resolved before parsing):

```yaml
server:
  host: "0.0.0.0"
  port: 5010
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
```

---

### 1.1 Core Value Objects

```python
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

@dataclass(frozen=True)
class AuthContext:
    public_key: str      # tenant/user from X-Group-ID or Basic auth
    is_org_admin: bool = False


@dataclass(frozen=True)
class DecryptedCredentials:
    """Transient — never persisted, never serialised to a response."""
    # S3
    access_key_id: str = ""
    secret_access_key: str = ""
    bucket: str = ""
    region: str = "us-east-1"
    key_prefix: str = ""
    # ClickHouse / Trino
    user: str = ""
    password: str = ""
    database: str = "default"
    table: str = "llogr_events"
    catalog: str = ""
    schema_name: str = ""
    # Langfuse
    public_key: str = ""
    secret_key: str = ""
    # Dataset sink
    api_token: str = ""
```

### 1.2 Database Row

```python
@dataclass
class ConnectionRow:
    """Maps 1-to-1 with the `connections` PostgreSQL table."""
    id: UUID
    owner_key: str                   # auth.public_key — tenant scope
    label: str
    type: str                        # "s3"|"clickhouse"|"trino"|"langfuse"|"dataset"
    role: str                        # "source" | "sink"
    connection_url: str              # plaintext — endpoint only, not secret
    credentials_enc: bytes           # Fernet-encrypted JSON of DecryptedCredentials fields
    status: str                      # "untested" | "reachable" | "unreachable"
    last_tested_at: datetime | None
    created_at: datetime
    updated_at: datetime
```

---

## 2. API Request / Response Models (Pydantic)

```python
from __future__ import annotations
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID
from pydantic import BaseModel, Field


# --- Credential payloads (write-only: accepted on create/patch, never returned) ---

class S3Credentials(BaseModel):
    access_key_id: str
    secret_access_key: str
    bucket: str
    region: str = "us-east-1"
    key_prefix: str = ""

class ClickHouseCredentials(BaseModel):
    user: str
    password: str
    database: str = "default"
    table: str = "llogr_events"

class TrinoCredentials(BaseModel):
    user: str
    password: str = ""
    catalog: str
    schema_name: str

class LangfuseCredentials(BaseModel):
    public_key: str
    secret_key: str

class DatasetSinkCredentials(BaseModel):
    api_token: str = ""

AnyCredentials = (
    S3Credentials
    | ClickHouseCredentials
    | TrinoCredentials
    | LangfuseCredentials
    | DatasetSinkCredentials
)


# --- Connection CRUD ---

class ConnectionCreate(BaseModel):
    label: Annotated[str, Field(min_length=1, max_length=120)]
    type: Literal["s3", "clickhouse", "trino", "langfuse", "dataset"]
    role: Literal["source", "sink"]
    connection_url: Annotated[str, Field(min_length=1)]
    credentials: AnyCredentials

class ConnectionPatch(BaseModel):
    label: str | None = None
    credentials: AnyCredentials | None = None

class ConnectionResponse(BaseModel):
    id: UUID
    label: str
    type: str
    role: str
    connection_url: str
    status: Literal["untested", "reachable", "unreachable"]
    system: bool           # True for system sources (config.yaml); False for user-owned
    last_tested_at: datetime | None
    created_at: datetime | None   # None for system sources (no DB row)
    updated_at: datetime | None   # None for system sources
    # credentials are NEVER included

class ConnectionListResponse(BaseModel):
    items: list[ConnectionResponse]
    # Contains both user-owned connections (system=False) and system sources (system=True).
    # User connections ordered by created_at DESC; system sources in config declaration order.


# --- Ping ---

class PingResponse(BaseModel):
    status: Literal["reachable", "unreachable"]
    latency_ms: float | None = None
    error: str | None = None


# --- Preview ---

class PreviewRequest(BaseModel):
    query: str = ""
    start: datetime | None = None
    end: datetime | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 50

class PreviewResponse(BaseModel):
    results: list[dict[str, Any]]
    connection_id: UUID


# --- Schema discovery ---

class SchemaField(BaseModel):
    type: Literal["bool", "int", "float", "list", "object", "string"]
    example: Any

class SchemaResponse(BaseModel):
    fields: dict[str, SchemaField]
    sample_count: int
    connection_id: UUID


# --- Health probes ---

ComponentState = Literal["ok", "degraded", "disabled"]

class ReadyResponse(BaseModel):
    status: Literal["ok", "degraded"]
    components: dict[str, ComponentState]
    # e.g. {"db": "ok", "prod-clickhouse": "ok", "prod-s3": "degraded"}

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    components: dict[str, ComponentState]
    details: dict[str, str] | None  # only degraded components; null when all ok


# --- UI config ---

class UiConfigResponse(BaseModel):
    connection_types: list[str]   # ["s3", "clickhouse", "trino", "langfuse", "dataset"]
    hide_auth_inputs: bool        # from settings.server.hide_auth_inputs
```

---

## 3. Adapter Protocol

```python
from typing import Protocol

class ConnectionAdapter(Protocol):
    async def ping(self) -> None:
        """Raise an exception if the backend is unreachable."""
        ...

    async def preview(
        self,
        query: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> list[dict]:
        """Return up to `limit` records matching `query` within [start, end]."""
        ...

    async def schema(
        self,
        start: datetime | None,
        end: datetime | None,
    ) -> dict[str, dict]:
        """Return {field_path: {type, example}} inferred from a sampled read."""
        ...


# Factory — the only dispatch point; no `conn.type ==` branches elsewhere
_REGISTRY: dict[str, type[BaseAdapter]] = {
    "s3":          S3ConnectionAdapter,
    "clickhouse":  ClickHouseConnectionAdapter,
    "trino":       TrinoConnectionAdapter,
    "langfuse":    LangfuseConnectionAdapter,
    "dataset":     DatasetSinkConnectionAdapter,
}

def get_adapter(
    source: ConnectionRow | SystemSourceConfig,
    creds: DecryptedCredentials,
) -> ConnectionAdapter:
    """Single dispatch point. No conn.type == branches anywhere else."""
    source_type = source.type
    cls = _REGISTRY.get(source_type)
    if cls is None:
        raise ValueError(f"Unknown connection type: {source_type!r}")
    return cls(source, creds)
```

---

## 4. Database Schema

```sql
-- Alembic-managed. All timestamps TIMESTAMPTZ (UTC).

CREATE TABLE connections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_key       TEXT        NOT NULL,
    label           TEXT        NOT NULL,
    type            TEXT        NOT NULL,   -- s3|clickhouse|trino|langfuse|dataset
    role            TEXT        NOT NULL,   -- source|sink
    connection_url  TEXT        NOT NULL,   -- plaintext endpoint URL
    credentials_enc BYTEA       NOT NULL,   -- Fernet-encrypted JSON
    status          TEXT        NOT NULL DEFAULT 'untested',
    last_tested_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON connections (owner_key);   -- all queries scope by owner_key
```

**Ownership rule**: every SELECT/UPDATE/DELETE includes `AND owner_key = $n` using `auth.public_key`. A row not owned by the caller is treated as 404, not 403 — avoids leaking existence.

---

## 5. UI Element Map (`data-testid`)

All interactive elements carry a `data-testid` used exclusively by Playwright tests (`getByTestId`).

| Element | `data-testid` | Notes |
|---------|--------------|-------|
| Page heading | `#page-title` | |
| Add connection button | `#add-connection-btn` | Opens modal |
| Connection form — label | `#conn-label-input` | |
| Connection form — type | `#conn-type-select` | `<select>` |
| Connection form — role | `#conn-role-select` | |
| Connection form — URL | `#conn-url-input` | |
| Connection form — submit | `#conn-submit-btn` | |
| Connection form — test (pre-save) | `#conn-test-btn` | |
| Connection list container | `#connections-list` | |
| Connection card (per item) | `#conn-card-{id}` | JS-rendered |
| Connection card — status badge | `#conn-status-{id}` | |
| Connection card — ping button | `#conn-ping-btn-{id}` | |
| Connection card — preview button | `#conn-preview-btn-{id}` | |
| Connection card — edit button | `#conn-edit-btn-{id}` | |
| Connection card — delete button | `#conn-delete-btn-{id}` | |
| Preview panel | `#preview-panel` | |
| Preview results table | `#preview-table` | |
| Preview query input | `#preview-query-input` | |
| Preview time-start | `#preview-start-input` | |
| Preview time-end | `#preview-end-input` | |
| Preview submit | `#preview-submit-btn` | |
| Schema panel | `#schema-panel` | |
| Schema field list | `#schema-fields` | |
| Schema discover button | `#schema-discover-btn` | |
| Empty state (user connections) | `#empty-state` | Shown when 0 user-owned connections |
| Error toast | `#error-toast` | JS-rendered |
| Success toast | `#success-toast` | JS-rendered |
| System Sources section | `#system-sources-section` | Separate from user connections |
| System source card | `#sys-card-{id}` | JS-rendered; no edit/delete buttons |
| System source status badge | `#sys-status-{id}` | |
| System source ping button | `#sys-ping-btn-{id}` | |
| System source preview button | `#sys-preview-btn-{id}` | |

---

## 6. State Transitions

```
Connection status
                      ┌─────────────┐
           create ──► │  untested   │
                      └──────┬──────┘
                             │ ping → OK
                      ┌──────▼──────┐
           ┌──────────│  reachable  │◄────────┐
           │          └─────────────┘         │
           │ ping → fail               ping → OK
           │          ┌─────────────┐         │
           └─────────►│ unreachable │─────────┘
                      └─────────────┘
```

- Status is updated **only** by an explicit ping (manual or triggered by preview/schema on first use).
- Saving updated credentials resets status to `untested`.
