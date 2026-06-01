# Research: Datasource Connection Management

**Phase 0 output** | **Date**: 2026-06-01

---

## Decision 1 — Storage: PostgreSQL via asyncpg + Alembic

**Decision**: Use PostgreSQL with asyncpg (async driver, no ORM) and Alembic for migrations.

**Rationale**: The dataimporter new-service-brief explicitly recommends PostgreSQL over SQLite for connection storage. asyncpg is the fastest async PostgreSQL driver for Python and avoids SQLAlchemy overhead. Alembic provides versioned, reviewable migrations without an ORM dependency. The docker-compose in ai-suite already has a PostgreSQL service available.

**Alternatives considered**:
- SQLite + aiosqlite: simpler setup, but no concurrent write safety, no foreign key enforcement by default, and dataimporter's own brief rejected it for the production design.
- SQLAlchemy async: adds ~50 kB dependency and ORM complexity for what is essentially three tables with simple queries.

---

## Decision 2 — Credential Encryption: `cryptography.Fernet`

**Decision**: Encrypt credential JSON with `cryptography.Fernet` (AES-128-CBC + HMAC-SHA256). Key supplied via `encryption_key` field in `config.yaml` (typically `vault:DATABRIDGE_ENCRYPTION_KEY`); resolved to a base64-urlsafe 32-byte key at startup.

**Rationale**: Same choice as dataimporter's new-service-brief and widely established in the codebase. Fernet is authenticated encryption — it prevents both tampering and decryption without the key. The connection URL is stored plaintext (it's an endpoint, not a secret) enabling queries and display without decryption. Key is now in YAML (with vault reference) rather than a direct env var — consistent with the "YAML-first config" decision.

**Alternatives considered**:
- AWS KMS / Vault: correct for multi-tenant cloud but introduces external dependencies that don't exist in the current ai-suite stack.
- Per-row random key: stronger but requires key-per-row storage, complicating key rotation without additional infrastructure.

---

## Decision 3 — Adapter Pattern: Internal protocol, no dataimporter import

**Decision**: Define a `ConnectionAdapter` protocol in `src/databridge/adapters.py` with `ping()`, `preview()`, and `schema()` methods. Each backend type gets its own adapter class. `get_adapter(conn_row, creds_dict) -> ConnectionAdapter` is the single factory.

**Rationale**: dataimporter uses the same architectural pattern (`DatasourceAdapter` + `_REGISTRY`). Reusing it as a library import is rejected because databridge is an independent service — shared library coupling would mean dataimporter's internal refactors break databridge. The adapter protocol is small enough to re-implement independently.

**Key constraint from dataimporter research**:
- S3 + DuckDB calls are **blocking** — must run via `asyncio.to_thread`
- All other backends (ClickHouse, Trino, Langfuse) are async HTTP via `httpx`

**Alternatives considered**:
- Import dataimporter as a Python package: rejected — circular dependency risk, dataimporter's adapters expose internal Datasource dataclass not appropriate for databridge's Connection model.
- Shared adapter library package: correct for long-term, out of scope for Phase 1.

---

## Decision 4 — Authentication: X-Group-ID header (nginx upstream)

**Decision**: `get_auth()` dependency reads `X-Group-ID` header as `public_key`. Optional `Authorization: Basic` fallback for SDK/direct access. No new auth system introduced.

**Rationale**: Identical to dataimporter. The reverse proxy (nginx in docker-compose) is the trust boundary. This avoids introducing a separate auth service dependency for Phase 1.

**Key sanitization rule** (from dataimporter auth.py): strip `..` path traversal and unsafe characters from `public_key`. Empty key after sanitization → 401.

---

## Decision 5 — UI: Vanilla-JS SPA co-served by FastAPI

**Decision**: Serve `browser.html` + `browser.js` + `browser.css` from the FastAPI process via Jinja2 + StaticFiles. Tailwind CSS from CDN. No build pipeline.

**Rationale**: Identical to dataimporter's browser UI approach, which proved sufficient for an internal tool. No separate Node.js process, no webpack, no npm. Users are internal — CDN Tailwind load time (~300 ms cold) is acceptable.

**UI `data-testid` strategy**: All static HTML elements get `data-testid` at authoring time. JS-generated elements (connection cards, form fields) get `data-testid` in the JS HTML-string templates. Playwright E2E uses `getByTestId()` exclusively.

---

## Decision 6 — Testing strategy

**Decision**: Three-tier test suite:
1. **Unit** (`tests/unit/`): pytest + FastAPI `TestClient` + dependency overrides. Mock at the adapter boundary with `FakeAdapter`. Test crypto, adapter dispatch, and no-type-branch invariants.
2. **Integration** (`tests/integration/`): pytest-asyncio + real asyncpg pool against a test PostgreSQL database. `respx` for mocking HTTP backends (ClickHouse, Trino, Langfuse). `moto[s3]` for S3. Tests the full route → DB → adapter path.
3. **E2E** (`tests/e2e/`): Playwright against a running service with a live PostgreSQL. Tests add, ping, preview, delete flows via `data-testid`.

**TDD order** (per Constitution §III):
1. Commit failing test stubs (all `pytest.mark.skip("not implemented")` removed one by one)
2. Implement to make each test pass
3. Refactor under green

**Integration test DB**: Use `pytest-asyncio` fixture that creates a fresh schema on a `databridge_test` database, runs the Alembic migrations, and drops it after the test session.

---

## Decision 7 — Project bootstrap: rename pyproject.toml

**Decision**: Update `pyproject.toml` to `name = "databridge"` and add missing dependencies. Remove `arq` (not needed for Phase 1). Remove `python-dotenv` (config is YAML-based, not .env-based).

**Runtime dependencies to add**:
```
asyncpg>=0.30
cryptography>=44.0
alembic>=1.16
psycopg2-binary>=2.9   # Alembic sync runner only
pyyaml>=6.0            # YAML config loader
```

**Dependencies to keep from existing pyproject.toml**: fastapi, uvicorn, httpx, aioboto3, duckdb, structlog, prometheus-fastapi-instrumentator, jinja2, python-multipart

**Dev dependencies to add**: `pytest-bdd>=7.0`, `anyio[trio]>=4.0`

---

## Decision 8 — Configuration System: YAML + vault + $VAR expansion

**Decision**: All service configuration in `config.yaml` (four top-level keys: `server`, `database_url`, `encryption_key`, `datasources`). Secret injection via two mechanisms (mixed in same file): `vault:KEYNAME` (resolved from Vault sidecar at `VAULT_SECRETS_PATH`, default `/vault/secrets/env`) and `$VAR` / `${VAR}` env-var expansion. Two env vars used: `DATABRIDGE_CONFIG` (config file path) and `VAULT_SECRETS_PATH`. Config is validated strictly — unknown keys raise `ValueError` immediately. `Settings` is a frozen dataclass cached via `@lru_cache`.

**Config file location resolution (priority order)**:
1. `DATABRIDGE_CONFIG` env var (absolute path)
2. `config.yaml` two directories above `config.py` (local dev)
3. `config.yaml` in the current working directory (production default)

**Rationale**: Mirrors `configuration.md` (the service's own configuration spec). Eliminates env-var proliferation for application config. Vault sidecar injection is already used by ai-suite deployments.

**Alternatives considered**:
- `python-dotenv` `.env` files: rejected — user explicitly requested YAML-only config.
- Pydantic Settings with env vars: rejected for same reason.

---

## Decision 9 — System Sources: YAML `datasources` block

**Decision**: System sources (admin-configured, read-only for users) are declared under a `datasources` list in `config.yaml`, identical in structure to dataimporter's `datasources`. Each has `name`, `type`, and type-specific credential fields. The service loads them at startup, resolves secrets, and holds them in memory. Their IDs are deterministic UUID v5 derived from the source `name` — rename = new ID. They appear in `GET /api/v1/connections` as `system: true` items alongside user-owned connections.

**Config example**:
```yaml
datasources:
  - name: "prod-clickhouse"
    type: clickhouse
    url: "http://clickhouse:8123"
    database: "default"
    table: "llogr_events"
    user: "default"
    password: "vault:CH_PASSWORD"
```

**Rationale**: Reuses the exact config shape from `configuration.md`. Ops teams already know this format from dataimporter.

---

## Decision 10 — Health Probes: three-state per-component model

**Decision**: Adopt the health probe format from `service-metrics-health.md`: three-state (`ok`/`degraded`/`disabled`) per-component dict. `/ready` returns 200 when all enabled components are `ok`, 503 on any `degraded`. `/health` adds `version` and `details` dict. Phase 1 components: `db` (asyncpg pool) + one entry per system source. Component checks run concurrently via `asyncio.gather`.

**Rationale**: Specified in the service's own metrics spec. Richer than a simple `{"status": "ok"}` — ops teams can see exactly which component is degraded without querying logs.

---

## Decision 11 — Logging & Audit: structlog + audit events

**Decision**: JSON structured logging via structlog with the processor chain from `service-logging-audit-and-exceptions.md`. Request ID middleware generates/propagates `x-request-id`. Global FastAPI exception handler catches all unhandled exceptions → structured log + `{"detail": "Internal server error"}` 500. Credential redaction via `redact_headers()` in `security.py`. Required audit events for Phase 1: `authenticated`, `auth_rejected` (from auth module), plus standard per-request logging.

**Rationale**: Defined in first-party service spec. Consistent with ai-suite logging standards.

---

## Decision 12 — Metrics: standard HTTP metrics

**Decision**: Use `http_requests_total` (Counter, labels: method/endpoint/status_code) and `http_request_duration_seconds` (Histogram, labels: method/endpoint) via `PrometheusMiddleware` from `service-metrics-health.md`. Path normalization collapses UUID/ID segments to `/:id`. Multiprocess-safe `/metrics` endpoint via `PROMETHEUS_MULTIPROC_DIR`. No service-specific named metrics in Phase 1.

**Rationale**: Defined in first-party service spec. Standard label names are compatible with existing Grafana dashboards in ai-suite.
