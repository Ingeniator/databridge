# Tasks: Datasource Connection Management

**Input**: Design documents from `specs/changes/001-datasource-connection-sync/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/openapi.yaml ✅

**Architecture sequence (Constitution §VII)**: Failing tests → Models → Adapters → Routes → SPA

**Tests**: Included — TDD is NON-NEGOTIABLE per Constitution §III. Failing test stubs are committed before any implementation task in each phase.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no shared dependencies)
- **[US#]**: User story this task belongs to
- File paths use `src/databridge/` layout from plan.md

---

## Phase 1: Setup

**Purpose**: Bootstrap the databridge Python package from the existing pyproject.toml stub.

- [X] T001 Rename `name = "dataimporter"` to `name = "databridge"` in `pyproject.toml`; add runtime deps: `asyncpg>=0.30`, `cryptography>=44.0`, `alembic>=1.16`, `psycopg2-binary>=2.9`, `pyyaml>=6.0`, `prometheus-client>=0.21`; remove `arq`, `python-dotenv`, and `prometheus-fastapi-instrumentator` (config is YAML-based; metrics use custom `PrometheusMiddleware` with `prometheus-client` directly)
- [X] T002 Add missing dev deps to `pyproject.toml`: `pytest-bdd>=7.0`, `anyio[trio]>=4.0`
- [X] T003 Create source package skeleton: `src/databridge/__init__.py`, `src/databridge/main.py`, `src/databridge/config.py`, `src/databridge/auth.py`, `src/databridge/metrics.py`, `src/databridge/crypto.py`, `src/databridge/adapters.py`
- [X] T004 Create sub-package skeletons: `src/databridge/db/__init__.py`, `src/databridge/db/pool.py`, `src/databridge/db/connections.py`, `src/databridge/routes/__init__.py`, `src/databridge/routes/deps.py`, `src/databridge/routes/connections.py`, `src/databridge/routes/health.py`, `src/databridge/routes/ui.py`
- [X] T005 Create static/template dirs: `src/databridge/static/.gitkeep`, `src/databridge/templates/.gitkeep`
- [X] T006 Create test skeleton: `tests/__init__.py`, `tests/conftest.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/e2e/__init__.py`
- [X] T007 Create `config.yaml.example` with all four YAML sections (`server`, `database_url`, `encryption_key`, `datasources`) and commented examples showing `vault:KEY` and `$VAR` secret injection; also document the two env vars (`DATABRIDGE_CONFIG`, `VAULT_SECRETS_PATH`) in a comment header
- [X] T008 Initialise Alembic: run `uv run alembic init src/databridge/db/migrations` and configure `env.py` to call `get_settings().database_url` (loaded from `config.yaml`) — NOT from a `DATABRIDGE_DATABASE_URL` env var
- [X] T009 [P] Create `Makefile` with targets: `dev`, `test`, `test-unit`, `test-integration`, `test-e2e`, `migrate`, `lint`
- [X] T010 [P] Create `docker-compose.override.yml` (databridge service + postgres dependency) for local dev

**Checkpoint**: `uv run pytest --collect-only` runs without import errors.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before any user story work. All cross-cutting modules that every route depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Failing tests for foundational modules

> **Write these FIRST — they must FAIL before proceeding to implementation.**

- [X] T011 [P] Write failing unit tests for `crypto.py` in `tests/unit/test_crypto.py`: round-trip encrypt/decrypt, empty payload, large payload (>1 MB), invalid ciphertext raises exception
- [X] T012 [P] Write failing unit tests for `auth.py` in `tests/unit/test_auth.py`: valid X-Group-ID, Basic auth fallback, path-traversal stripping, empty key → 401; assert `authenticated` structured log event emitted on success (use `structlog.testing.capture_logs`); assert `auth_rejected` event emitted on failure with `reason` and `path` fields
- [X] T024a [P] Write failing unit tests `tests/unit/test_config.py`: valid YAML loads correctly; vault reference resolved from sidecar file; `$VAR` expansion; unknown key raises `ValueError`; missing config file raises `FileNotFoundError` with path; unresolvable vault reference raises `ValueError` naming the missing key; `get_settings()` returns same singleton on second call

### Foundational implementation

- [X] T013 Implement `src/databridge/config.py`: YAML loader with `_find_config()` priority-order resolution (`DATABRIDGE_CONFIG` env var → two-dirs-up `config.yaml` → cwd `config.yaml`); vault sidecar resolution (`vault:KEY` → read from `VAULT_SECRETS_PATH` file, default `/vault/secrets/env`); `os.path.expandvars` for `$VAR` expansion; `ServerConfig`, `SystemSourceConfig`, `Settings` frozen dataclasses per `data-model.md §1.0`; strict unknown-key validation (unknown keys raise `ValueError`); `@lru_cache` singleton via `get_settings()`; fail fast on missing config file or unresolvable vault reference
- [X] T013a [P] Implement `src/databridge/security.py`: `redact_headers(headers: dict) -> dict` — returns a copy with sensitive header values (Authorization, x-api-key, x-token, cookie, set-cookie, proxy-authorization) masked to `first4...[REDACTED]` per `service-logging-audit-and-exceptions.md §6`
- [X] T013b [P] Implement `src/databridge/logging_config.py`: `setup_logging(debug: bool, silence_probes: bool)` — structlog processor chain (merge_contextvars → filter_by_level → add_logger_name → add_log_level → TimeStamper(iso) → format_exc_info → JSONRenderer in prod / ConsoleRenderer in debug); `SilenceProbesFilter` on `uvicorn.access` when `silence_probes=True`; call from `main.py` lifespan startup per `service-logging-audit-and-exceptions.md §1`
- [X] T014 [P] Implement `src/databridge/crypto.py`: `encrypt_credentials(creds: dict) -> bytes` and `decrypt_credentials(ct: bytes) -> dict` using `cryptography.Fernet`; key from `Settings.encryption_key`
- [X] T015 [P] Implement `src/databridge/auth.py`: `AuthContext(public_key, is_org_admin)` NamedTuple; `get_auth()` FastAPI dependency reading `X-Group-ID` with `Authorization: Basic` fallback; strip path-traversal from `public_key`; return 401 if empty after sanitisation
- [X] T016 [P] Implement `src/databridge/metrics.py`: `PrometheusMiddleware` with `http_requests_total` Counter (labels: method, endpoint, status_code) + `http_request_duration_seconds` Histogram (labels: method, endpoint); path normalization via `_PATH_ID_RE` collapsing UUID/numeric segments to `/:id`; skip metrics for streaming/multipart requests; multiprocess-safe `/metrics` endpoint via `PROMETHEUS_MULTIPROC_DIR`; register middleware in `main.py` in correct Starlette order (`RequestID → Logging → Prometheus → handler`) per `service-metrics-health.md §1-§6`
- [X] T017 Write Alembic migration `src/databridge/db/migrations/versions/0001_connections.py`: create `connections` table per `data-model.md §4`; include `CREATE INDEX ON connections (owner_key)`; also create empty `sync_jobs (id UUID PRIMARY KEY, connection_id UUID REFERENCES connections(id))` stub table so the Phase 5 deletion guard (T048) never needs a `try/except` on table existence
- [X] T018 Implement `src/databridge/db/pool.py`: `create_pool()` async context manager using `asyncpg.create_pool(dsn=settings.database_url)`; `get_pool()` FastAPI dependency
- [X] T019 Implement `src/databridge/routes/deps.py`: re-export `get_auth` and `get_pool`; add `get_connection_or_404(id, pool, auth)` helper that queries by `id AND owner_key = auth.public_key`, returning 404 if missing
- [X] T020 Implement `src/databridge/routes/health.py`: three endpoints per `contracts/openapi.yaml` + FR-020:
  - `GET /livez` — always 200 `{"status": "ok"}`, no dependency checks
  - `GET /ready` — run all component checks concurrently via `asyncio.gather`; components: `db` (asyncpg pool ping) + one entry per `settings.datasources` item; return 200 `ReadyResponse` when all `ok`, 503 when any `degraded`; `disabled` (unconfigured) components excluded from status
  - `GET /api/v1/health` — same checks as `/ready` + `version` field + `details` dict for degraded components (error string, not traceback)
- [X] T021 Implement `src/databridge/main.py`: FastAPI app factory `create_app()`; mount health, connection, and UI routers; register `PrometheusMiddleware` from `metrics.py` (custom middleware, NOT `prometheus-fastapi-instrumentator`); `lifespan` opens/closes DB pool and calls `setup_logging()`
- [X] T022 [P] Write failing integration tests `tests/integration/test_health.py`: GET /livez → 200 `{"status":"ok"}`; GET /ready → 200 with `{"status":"ok","components":{"db":"ok"}}` when DB up; GET /ready → 503 with `{"status":"degraded","components":{"db":"degraded"}}` when DB down (override pool dependency); GET /api/v1/health → includes `version` field and `details: null` when healthy
- [X] T023 Run `uv run alembic upgrade head` against test DB and verify schema; fix migration if it fails
- [X] T024 [P] Implement `tests/conftest.py`: `asyncpg_pool` fixture (creates `databridge_test` schema, runs migrations, drops after session); `test_client` fixture with pool and auth overrides; `fake_auth` fixture returning `AuthContext(public_key="test-user")`; **`config_file` fixture** that writes a minimal `config.yaml` to a `tmp_path` and sets `DATABRIDGE_CONFIG` env var for the test session (required since config.py reads YAML, not env vars)
- [X] T024c [P] Write failing integration tests `tests/integration/test_system_sources.py` (MUST be committed before T024b): GET /api/v1/connections with two system sources in config → response items include `system=true` items with correct type/label/connection_url; system source `id` is deterministic UUID v5 of name; POST /api/v1/connections/{system-source-id} returns 404 (can't create); PATCH and DELETE on system source ID return 404; POST /api/v1/connections/{system-source-id}/ping → returns PingResponse (using config credentials); POST /api/v1/connections/{system-source-id}/preview → returns PreviewResponse
- [X] T024b Implement system source loading in `src/databridge/routes/connections.py` and `routes/deps.py`: add `get_system_sources() -> list[SystemSourceConfig]` dependency that reads `get_settings().datasources`; update `GET /api/v1/connections` handler to merge system sources (as `ConnectionResponse` items with `system=True`, `created_at=None`, `updated_at=None`) with user-owned connections (system=False) in a single response; system sources appear after user connections, ordered by config declaration order

**Checkpoint**: `uv run pytest tests/unit tests/integration/test_health.py -v` → all pass.

---

## Phase 3: User Story 1 — Add and Test a New Connection (Priority: P1) 🎯 MVP

**Goal**: Full CRUD for connections + ping endpoint. A user can add any connection type, save it, test reachability, and delete it — entirely via the API.

**Independent Test**: `POST /api/v1/connections` (ClickHouse) → `GET /api/v1/connections` → `POST /api/v1/connections/{id}/ping` → `DELETE /api/v1/connections/{id}` all return expected shapes with no credentials in responses.

### Failing tests — write FIRST (must FAIL before T033)

- [X] T025 [P] [US1] Write failing unit test in `tests/unit/test_adapters.py`: `get_adapter()` returns correct adapter class per type; assert no `conn.type ==` branches and no `"from databridge.backends"` direct imports in `src/databridge/routes/connections.py` and `src/databridge/adapters.py` source text
- [X] T025a [P] [US1] Write failing integration test in `tests/integration/test_connections_test_endpoint.py`: `POST /api/v1/connections/test` with valid ClickHouse creds (respx mock) → `{status: "reachable"}`; with unreachable URL → `{status: "unreachable"}`; with missing `type` field → 400; without auth → 401
- [X] T026 [P] [US1] Write failing integration tests in `tests/integration/test_connections_crud.py`: POST create → 201 with no credentials in body; GET list → returns owned connections only; GET by id → 200; GET by id (wrong owner) → 404; DELETE → 204; DELETE (already deleted) → 404
- [X] T027 [P] [US1] Write failing integration tests in `tests/integration/test_connections_ping.py`: POST ping on reachable mock → `{status: "reachable", latency_ms: <float>}`; POST ping on unreachable mock → `{status: "unreachable", error: ...}`; assert ping completes within 5s (mock timeout fixture); verify DB `status` and `last_tested_at` updated after ping

### Implementation

- [X] T028 [US1] Implement Pydantic models in `src/databridge/models.py`: `S3Credentials`, `ClickHouseCredentials`, `TrinoCredentials`, `LangfuseCredentials`, `DatasetSinkCredentials`, `ConnectionCreate`, `ConnectionPatch`, `ConnectionResponse` (include `system: bool = False`; `created_at: datetime | None`; `updated_at: datetime | None`), `ConnectionListResponse`, `PingResponse`, `ReadyResponse`, `HealthResponse` — per `data-model.md §2`
- [X] T029 [US1] Implement `ConnectionRow` dataclass in `src/databridge/db/connections.py`; add `insert_connection()`, `get_connection()`, `list_connections()`, `update_connection()`, `delete_connection()`, `update_connection_status()` async functions using raw asyncpg queries with `owner_key` scope on every query
- [X] T030 [P] [US1] Implement `src/databridge/adapters.py` — HTTP adapters only: `ConnectionAdapter` Protocol with `ping()` async method; `BaseAdapter.__init__(conn, creds)`; `ClickHouseConnectionAdapter.ping()` via `GET {url}/ping` (httpx, **5 s timeout**); `TrinoConnectionAdapter.ping()` via `GET {url}/v1/info` (**5 s timeout**); `LangfuseConnectionAdapter.ping()` via `GET {url}/api/public/health` (**5 s timeout**); `DatasetSinkConnectionAdapter.ping()` via `GET {url}/health` (**5 s timeout**); `_REGISTRY` dict; `get_adapter()` factory
- [X] T030a [US1] Implement `POST /api/v1/connections/test` route in `src/databridge/routes/connections.py`: accept `{type, connection_url, credentials}`, build a transient `DecryptedCredentials` and call `get_adapter()` with a synthetic `ConnectionRow` (no DB write), call `adapter.ping()`, return `PingResponse`; wire into router — per `contracts/openapi.yaml`
- [X] T031 [P] [US1] Implement `S3ConnectionAdapter.ping()` in `src/databridge/adapters.py`: use aioboto3 `head_bucket`; run via `asyncio.to_thread` — S3 calls are blocking; apply **5 s** overall timeout via `asyncio.wait_for`
- [X] T032 [US1] Implement `src/databridge/routes/connections.py`: `POST /api/v1/connections` (encrypt creds, insert, return response), `GET /api/v1/connections` (list by owner), `GET /api/v1/connections/{id}`, `DELETE /api/v1/connections/{id}`, `POST /api/v1/connections/{id}/ping` (decrypt → `get_adapter().ping()` → update status → return `PingResponse`) — per `contracts/openapi.yaml`
- [X] T033 [US1] Implement `PATCH /api/v1/connections/{id}` in `src/databridge/routes/connections.py`: update label if provided; if credentials provided, re-encrypt and reset `status = "untested"`, clear `last_tested_at`
- [X] T034 [US1] Wire `connections` router into `src/databridge/main.py`; verify `GET /api/v1/connections` returns 401 without auth header
- [X] T035 [US1] Run failing tests T025, T025a, T026, T027; fix implementation until all pass; confirm no `conn.type ==` branch appears in route files

**Checkpoint**: All CRUD + ping tests pass. `curl -H 'X-Group-ID: u1' POST /api/v1/connections` creates a connection; the response contains no credential fields; DELETE removes it; second GET returns 404.

---

## Phase 4: User Story 2 — Browse and Preview Data (Priority: P2)

**Goal**: `POST /api/v1/connections/{id}/preview` and `GET /api/v1/connections/{id}/schema` endpoints. Users can sample live data and discover fields from any saved source connection.

**Independent Test**: Select a saved ClickHouse or Langfuse connection, call `/preview` with a 24-hour window, receive ≥1 record in `results`. Call `/schema`, receive `fields` dict with at least `session_id` and `timestamp`.

### Failing tests — write FIRST

- [X] T036 [P] [US2] Write failing integration tests in `tests/integration/test_connections_preview.py`: POST preview on source connection with `respx` mock → returns `PreviewResponse` with `results` list; POST preview on sink connection → 400; POST preview with invalid credentials → 502
- [X] T037 [P] [US2] Write failing integration tests in `tests/integration/test_connections_schema.py`: GET schema on source connection → returns `SchemaResponse` with non-empty `fields` dict; GET schema on sink → 400; verify `sample_count` matches sampled records

### Implementation

- [X] T038 [US2] Add `PreviewRequest`, `PreviewResponse`, `SchemaField`, `SchemaResponse` Pydantic models to `src/databridge/models.py`
- [X] T039 [US2] Add `preview(query, start, end, limit) -> list[dict]` method to each adapter in `src/databridge/adapters.py`: `ClickHouseConnectionAdapter` (SQL `WHERE ... LIKE`), `TrinoConnectionAdapter`, `LangfuseConnectionAdapter` (REST traces endpoint), `DatasetSinkConnectionAdapter` raises `NotImplementedError`
- [X] T040 [US2] Add `S3ConnectionAdapter.preview()` to `src/databridge/adapters.py`: list keys in bucket, DuckDB content scan via `asyncio.to_thread`
- [X] T041 [US2] Add `schema(start, end) -> dict[str, dict]` method to each adapter in `src/databridge/adapters.py`: time-bucketed sampling (split `[start,end]` into 5 sub-windows, call `preview("", start=window_start, end=window_end, limit=1)` concurrently for each window, merge results + infer types); `DatasetSinkConnectionAdapter` raises `NotImplementedError`
- [X] T042 [US2] Implement `_infer_schema(records: list[dict]) -> dict[str, SchemaField]` helper in `src/databridge/adapters.py`: flatten nested dicts to depth 3 with dot-keys, skip `_`-prefixed keys, infer type from Python type, capture example value
- [X] T043 [US2] Add `POST /api/v1/connections/{id}/preview` route to `src/databridge/routes/connections.py`: reject `role=sink` with 400; decrypt creds → `get_adapter().preview()` → return `PreviewResponse`; catch adapter exceptions → 502
- [X] T044 [US2] Add `GET /api/v1/connections/{id}/schema` route to `src/databridge/routes/connections.py`: reject `role=sink` with 400; decrypt creds → `get_adapter().schema()` → return `SchemaResponse`; catch adapter exceptions → 502
- [X] T045 [US2] Run failing tests T036–T037; fix until all pass

**Checkpoint**: `POST /api/v1/connections/{id}/preview` returns records; `GET /api/v1/connections/{id}/schema` returns a field map. Sink connections return 400 on both endpoints.

---

## Phase 5: User Story 3 — Manage Multiple Connections Across Types (Priority: P3)

**Goal**: Multi-connection list with type badges, label rename, credential update, and deletion guard for future sync jobs.

**Independent Test**: Create S3, ClickHouse, and Langfuse connections as the same user; `GET /api/v1/connections` returns all three with correct `type`, `status`, `last_tested_at`; PATCH label on one; PATCH credentials on another (verify status resets to `untested`); DELETE third; list shows two remaining.

### Failing tests — write FIRST

- [X] T046 [P] [US3] Write failing integration tests in `tests/integration/test_connections_multi.py`: create 3 connections of different types, list shows all 3 ordered by `created_at DESC`; PATCH label only → status unchanged; PATCH credentials → status becomes `untested`, `last_tested_at` cleared; DELETE with referencing job returns 409 (use test fixture that inserts a stub job row)

### Implementation

- [X] T047 [US3] Verify `list_connections()` in `src/databridge/db/connections.py` orders by `created_at DESC`; add `ORDER BY created_at DESC` if missing
- [X] T048 [US3] Add `409` guard to `DELETE /api/v1/connections/{id}` in `src/databridge/routes/connections.py`: check `SELECT COUNT(*) FROM sync_jobs WHERE connection_id = $1` (stub table created by T017 migration — no `try/except` needed); return `{"detail": "connection is used by N sync job(s)"}` if count > 0
- [X] T049 [US3] Run failing tests T046; fix until all pass

**Checkpoint**: All three user stories pass independently. Full test suite runs green: `uv run pytest tests/unit tests/integration -v`.

---

## Phase 6: Browser SPA

**Purpose**: Vanilla-JS SPA co-served from FastAPI. Covers the UI surface of all three user stories.

- [X] T050 Implement `GET /api/v1/ui-config` route in `src/databridge/routes/ui.py`: return `UiConfigResponse` with `connection_types` and `hide_auth_inputs` flag
- [X] T051 Implement `src/databridge/routes/ui.py`: `GET /` serves `browser.html` via Jinja2; mount `src/databridge/static/` at `/static`; wire into `main.py`
- [X] T052 Write `src/databridge/templates/browser.html`: page shell with nav bar, `data-base` attribute for reverse-proxy compatibility, CDN Tailwind + Material Symbols + Google Fonts (Inter); empty `<div id="app">` target; all static element `data-testid` attributes per `data-model.md §5`
- [X] T053 Write `src/databridge/static/browser.js` — connections list view: on load fetch `GET /api/v1/ui-config` + `GET /api/v1/connections`; split response into `system=false` items (render as user connection cards `#conn-card-{id}`) and `system=true` items (render in `#system-sources-section` as `#sys-card-{id}` cards); user connection cards have all CRUD buttons (`#conn-ping-btn-{id}`, `#conn-edit-btn-{id}`, `#conn-delete-btn-{id}`, `#conn-preview-btn-{id}`); system source cards have only `#sys-ping-btn-{id}` and `#sys-preview-btn-{id}` (no edit/delete); `#empty-state` shown only when zero user-owned connections; `#system-sources-section` is always visible when system sources exist
- [X] T054 Write `src/databridge/static/browser.js` — add/edit connection modal: "Add Connection" button (`#add-connection-btn`) opens modal with type-conditional credential fields; **pre-save** `#conn-test-btn` calls `POST /api/v1/connections/test` with the unsaved form credentials (no ID required); **post-save** `#conn-ping-btn-{id}` calls `POST /api/v1/connections/{id}/ping`; `#conn-submit-btn` calls `POST /api/v1/connections` (create) or `PATCH /api/v1/connections/{id}` (edit); success closes modal and refreshes list; error shows `#error-toast`
- [X] T055 Write `src/databridge/static/browser.js` — preview panel (`#preview-panel`): clicking `#conn-preview-btn-{id}` populates `#preview-query-input`, `#preview-start-input`, `#preview-end-input`; `#preview-submit-btn` calls `POST /api/v1/connections/{id}/preview`; render results in `#preview-table`
- [X] T056 Write `src/databridge/static/browser.js` — schema panel (`#schema-panel`): `#schema-discover-btn` calls `GET /api/v1/connections/{id}/schema`; render field list in `#schema-fields` as a table with name, type, example columns
- [X] T057 Write `src/databridge/static/browser.css`: styles for runtime-generated elements (connection cards, type badges, modal overlay, toast notifications, preview table, schema field list)

**Checkpoint**: `open http://localhost:5010` → connections page loads, displays empty state, "Add Connection" opens modal, form submits successfully.

---

## Phase 7: E2E Tests (Playwright)

**Purpose**: Verify the complete user journey through the browser.

- [X] T058 [P] Write Playwright test `tests/e2e/test_connections_add.py`: navigate to `/`, click `#add-connection-btn`, fill ClickHouse form (using `data-testid` selectors exclusively), submit, verify `#conn-card-{id}` appears in list
- [X] T059 [P] Write Playwright test `tests/e2e/test_connections_ping.py`: click `#conn-ping-btn-{id}` on a user connection, wait for `#conn-status-{id}` to update; also click `#sys-ping-btn-{sys-id}` on a system source card in `#system-sources-section`, verify `#sys-status-{sys-id}` updates; confirm no edit/delete buttons on system source cards
- [X] T060 [P] Write Playwright test `tests/e2e/test_connections_preview.py`: click `#conn-preview-btn-{id}`, fill `#preview-query-input`, click `#preview-submit-btn`, verify `#preview-table` contains rows
- [X] T061 [P] Write Playwright test `tests/e2e/test_connections_delete.py`: click `#conn-delete-btn-{id}`, confirm deletion dialog, verify card removed from list; verify `#empty-state` shown when last connection deleted

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T062 [P] Write `tests/unit/test_performance.py`: (a) 50 concurrent `GET /api/v1/connections` → p95 ≤ 500 ms; (b) 10 concurrent `POST /api/v1/connections/{id}/preview` (respx mock, instant backend) → p95 ≤ 10 000 ms (SC-003); (c) 10 concurrent `GET /api/v1/connections/{id}/schema` → p95 ≤ 15 000 ms (SC-004); (d) 20 concurrent `POST /api/v1/connections/{id}/ping` → p95 ≤ 5 000 ms (SC-006); use `httpx.AsyncClient` + `asyncio.gather` for all cases
- [X] T063 [P] Write `tests/unit/test_no_type_branches.py`: for each file in `src/databridge/routes/connections.py` and `src/databridge/adapters.py` assert: (a) `"conn.type ==" not in src`; (b) `"connection.type ==" not in src`; (c) `"from databridge.backends" not in src` (no direct backend imports bypassing the adapter registry)
- [X] T064 Wire `logging_config.py` and `security.py` into `src/databridge/main.py`: call `setup_logging(debug=settings.server.debug, silence_probes=settings.server.silence_probes)` in `lifespan` startup; add `RequestIDMiddleware` (generate UUID4 if `x-request-id` absent, bind to structlog contextvars, return in response header); add global `exception_handler(Exception)` → structured `logger.error("unhandled_exception", exc_info=True)` + `{"detail": "Internal server error"}` 500; register middleware in Starlette order: `RequestID → LoggingMiddleware → PrometheusMiddleware` (outermost first) per `service-metrics-health.md §6`
- [X] T065 [P] Update `README.md` with service description, quickstart reference, and API endpoint list
- [X] T066 Run full test suite `uv run pytest -v`; fix any remaining failures
- [X] T067 Validate `quickstart.md` end-to-end: follow every step from "Prerequisites" through the curl examples; update any outdated commands

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Requires Phase 1 complete — **blocks all user story work**
- **Phase 3 (US1)**: Requires Phase 2 — no other story dependencies
- **Phase 4 (US2)**: Requires Phase 2 + Phase 3 adapter scaffold (T030–T031)
- **Phase 5 (US3)**: Requires Phase 2 + Phase 3 (CRUD routes must exist)
- **Phase 6 (UI)**: Requires Phases 3–5 (all API endpoints must exist)
- **Phase 7 (E2E)**: Requires Phase 6 complete
- **Phase 8 (Polish)**: Requires Phases 3–7

### Within Each Phase

- Failing test tasks (T011–T012, T025–T027, T036–T037, T046) MUST be committed before their corresponding implementation tasks
- Models before adapters; adapters before routes
- `tests/conftest.py` (T024) must exist before any integration test can run

### Parallel Opportunities (within Phase 2)

```
T011  (test_crypto)         ──┐
T012  (test_auth)             ├─ parallel (failing tests — commit before implementing)
T024a (test_config.py)        ┘
T013  (config.py)           ── after T024a committed (TDD)
T013a (security.py)         ──┐
T013b (logging_config.py)     ├─ parallel with T013 (different files)
T014  (crypto.py)             │
T015  (auth.py)               ┘
T016  (metrics.py)          ── parallel with above (different file)
T024c (test_system_sources) ── commit before T024b (TDD)
T024b (system source merge) ── after T024c committed
T017  (migration)           ── sequential (needed by T018)
T018  (db/pool.py)          ── sequential
T019  (routes/deps.py)      ── after T015 + T018
```

### Parallel Opportunities (within Phase 3)

```
T025  (test_adapters)        ──┐
T025a (test_test_endpoint)     ├─ parallel (different files)
T026  (test_crud)              ├─
T027  (test_ping)              ┘
T028 (models.py)        ── before T029, T030
T029 (db/connections)   ──┐
T030 (adapters HTTP)      ├─ parallel
T031 (S3 adapter)         ┘
T032 (routes CRUD)      ── after T029 + T030
T033 (routes PATCH)     ── after T032
```

---

## Implementation Strategy

### MVP (Phase 1 + 2 + 3 only)

1. Complete Phase 1 (Setup) + Phase 2 (Foundational)
2. Complete Phase 3 (US1 — Add and Test Connection)
3. **STOP and VALIDATE**: `uv run pytest tests/unit tests/integration -v` all green; curl walkthrough per quickstart.md
4. Ship MVP: full connection CRUD + ping via API

### Incremental Delivery

1. Phase 1 + 2 → Infrastructure ready
2. Phase 3 (US1) → **MVP: add, test, delete connections**
3. Phase 4 (US2) → Preview + schema discovery
4. Phase 5 (US3) → Multi-type management + delete guard
5. Phase 6 + 7 (UI + E2E) → Browser UI
6. Phase 8 (Polish) → Production-ready

---

## Notes

- `[P]` tasks operate on different files with no shared in-flight dependencies
- Failing test commit **must precede** implementation commit in git log (Constitution §III)
- Every route is covered by `http_requests_total` Counter + `http_request_duration_seconds` Histogram via `PrometheusMiddleware` (Constitution §VI — no per-route metric registration needed)
- Credential fields must never appear in any response body — add an assertion to `test_connections_crud.py` that inspects raw response JSON
- S3/DuckDB blocking operations must always use `asyncio.to_thread` — add to `test_no_type_branches.py` an assertion that `await.*duckdb` never appears outside a `to_thread` call
