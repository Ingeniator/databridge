from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# ── Credential payloads (write-only) ─────────────────────────────────────────

class S3Credentials(BaseModel):
    access_key_id: str
    secret_access_key: str
    bucket: str
    region: str = "us-east-1"
    key_prefix: str = ""
    endpoint: str = ""
    addressing_style: str = "virtual"


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
    table: str = "events"


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

# ── Connection CRUD ───────────────────────────────────────────────────────────

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
    system: bool = False
    last_tested_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ConnectionListResponse(BaseModel):
    items: list[ConnectionResponse]


# ── Ping ──────────────────────────────────────────────────────────────────────

class PingResponse(BaseModel):
    status: Literal["reachable", "unreachable"]
    latency_ms: float | None = None
    error: str | None = None
    auth_ok: bool | None = None       # None = not checked; True/False = credentials verified
    auth_error: str | None = None


# ── Pre-save test ─────────────────────────────────────────────────────────────

class ConnectionTestRequest(BaseModel):
    type: Literal["s3", "clickhouse", "trino", "langfuse", "dataset"]
    connection_url: str
    credentials: AnyCredentials


# ── Preview ───────────────────────────────────────────────────────────────────

class PreviewRequest(BaseModel):
    query: str = ""
    start: datetime | None = None
    end: datetime | None = None
    time_field: str | None = None
    limit: Annotated[int, Field(ge=1, le=100_000)] = 50


class PreviewResponse(BaseModel):
    results: list[dict[str, Any]]
    connection_id: UUID
    total_count: int = 0
    schema_fields: dict = Field(default_factory=dict)


# ── Schema discovery ──────────────────────────────────────────────────────────

class SchemaField(BaseModel):
    type: Literal["bool", "int", "float", "list", "object", "string"]
    example: Any = None


class SchemaResponse(BaseModel):
    fields: dict[str, SchemaField]
    sample_count: int
    connection_id: UUID


# ── Health probes ─────────────────────────────────────────────────────────────

ComponentState = Literal["ok", "degraded", "disabled"]


class ReadyResponse(BaseModel):
    status: Literal["ok", "degraded"]
    components: dict[str, ComponentState]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    components: dict[str, ComponentState]
    details: dict[str, str] | None = None


# ── UI config ─────────────────────────────────────────────────────────────────

class UiConfigResponse(BaseModel):
    connection_types: list[str]
    hide_auth_inputs: bool
    webhook_allowed_url_prefixes: list[str] = []
