# Implementation Plan: Datasource Connection Management

**Branch**: `001-datasource-connection-sync` | **Date**: 2026-06-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/changes/001-datasource-connection-sync/spec.md`

## Summary

Build the connection management foundation of the databridge service: a FastAPI backend + PostgreSQL store that lets users create, test, and browse data from named connections (S3, ClickHouse, Trino, Langfuse, dataset sinks) with credentials encrypted at rest. Administrator-configured system sources (defined in `config.yaml`) are served read-only alongside user connections. A vanilla-JS SPA served by the same process provides the UI. All service config is YAML-based with vault/env-var secret injection. This is Phase 1 — no sync scheduling.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: FastAPI 0.135+, uvicorn, asyncpg, cryptography (Fernet), alembic, pyyaml, httpx, aioboto3, duckdb, structlog, prometheus-client, jinja2

**Storage**: PostgreSQL — `connections` table with Fernet-encrypted credential column

**Testing**: pytest, pytest-asyncio (strict mode), respx (HTTP mocking), moto[s3], pytest-bdd (Gherkin stubs), playwright + pytest-playwright (E2E)

**Target Platform**: Linux server / docker-compose (same environment as ai-suite)

**Project Type**: Web service — FastAPI REST API + vanilla-JS SPA served from the same process

**Performance Goals**: All API endpoints p95 ≤ 500 ms; preview endpoint p95 ≤ 10 s for datasets up to 10 000 records

**Constraints**: All adapter I/O must be `async`; DuckDB/S3 blocking calls must run via `asyncio.to_thread`; no synchronous calls in the FastAPI event loop; two env vars only (`DATABRIDGE_CONFIG`, `VAULT_SECRETS_PATH`); all other config from YAML

**Scale/Scope**: Single-tenant per request (owner-scoped queries); expected <100 connections per user in v1

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| Model exists | ✅ | `data-model.md` created in Phase 1 |
| Contract exists | ✅ | `contracts/openapi.yaml` created in Phase 1 |
| Tests first | ✅ | Failing test stubs committed before implementation |
| Test IDs | ✅ | All UI elements carry `data-testid`; spec notation in `data-model.md` |
| Async I/O | ✅ | All adapter methods `async`; S3+DuckDB via `asyncio.to_thread` |
| Metrics | ✅ | Each new route exports Prometheus counter + histogram |
| Performance | ✅ | p95 ≤ 500 ms enforced in `tests/unit/test_performance.py` |

No violations — Complexity Tracking table omitted.

## Project Structure

### Documentation (this feature)

```text
specs/changes/001-datasource-connection-sync/
├── plan.md              ← this file
├── research.md          ← Phase 0 decisions (updated: decisions 8–12 added)
├── data-model.md        ← Pydantic models + DB schema + UI testid map + config YAML shape
├── quickstart.md        ← local dev setup
├── contracts/
│   └── openapi.yaml     ← full OpenAPI 3.1 blueprint
└── tasks.md             ← 70 tasks across 8 phases
```

### Source Code (repository root)

```text
src/databridge/
├── main.py                  # FastAPI app factory, router registration, lifespan
├── config.py                # Settings frozen dataclass + YAML loader with vault/env-var resolution;
│                            #   SystemSourceConfig dataclasses; @lru_cache singleton
├── logging_config.py        # setup_logging(debug, silence_probes) — structlog processor chain
├── auth.py                  # AuthContext NamedTuple + get_auth() dependency; audit events
│                            #   authenticated / auth_rejected; redact_headers() calls
├── security.py              # redact_headers(headers) — masks Authorization, x-api-key, etc.
├── metrics.py               # PrometheusMiddleware, http_requests_total, http_request_duration_seconds,
│                            #   path normalization (/:id), multiprocess-safe /metrics endpoint
├── crypto.py                # encrypt_credentials() / decrypt_credentials() via Fernet
├── adapters.py              # ConnectionAdapter protocol + BaseAdapter + 5 source adapters
│                            #   + DatasetSinkAdapter + get_adapter(conn_or_system, creds) factory
│
├── db/
│   ├── pool.py              # asyncpg pool lifecycle (get_pool() dependency)
│   ├── migrations/          # Alembic env.py + version scripts
│   └── connections.py       # ConnectionRow dataclass + insert/select/update/delete queries
│
├── routes/
│   ├── deps.py              # get_auth(), get_pool(), get_system_sources() shared dependencies
│   ├── connections.py       # CRUD + /ping + /preview + /schema + /test (transient ping)
│   ├── health.py            # GET /livez, /ready, /api/v1/health — three-state component model
│   └── ui.py                # Serve browser.html SPA + /api/v1/ui-config
│
├── templates/
│   └── browser.html         # SPA shell (Tailwind CDN, Material Symbols, data-testid on all elements)
│
└── static/
    ├── browser.js           # All SPA logic (~1 500 lines)
    └── browser.css          # Runtime-generated element styles

tests/
├── conftest.py              # asyncpg test pool, auth override, fake adapter fixtures
├── unit/
│   ├── test_crypto.py       # encrypt/decrypt round-trip, empty/large payloads
│   ├── test_adapters.py     # adapter dispatch, no-type-branch assertion
│   └── test_performance.py  # p95 latency guard (50 concurrent requests)
├── integration/
│   ├── test_connections_crud.py   # create/list/get/patch/delete against real PG
│   └── test_connections_ping.py   # ping + preview + schema via respx/moto stubs
└── e2e/
    └── test_connections_ui.py     # Playwright: add, test, preview, delete flows
```

**Structure Decision**: FastAPI web service with a flat `src/databridge/` layout, mirroring dataimporter's proven structure. Vanilla-JS SPA co-served eliminates a separate frontend process. PostgreSQL instead of SQLite — aligns with the dataimporter new-service-brief recommendation and avoids SQLite concurrency limits.

## Architecture Sequence (per Constitution §VII)

```
data-model.md  →  contracts/openapi.yaml  →  failing test stubs  →  implementation  →  refactor
```

1. Pydantic models + DB schema (`data-model.md`) — **done**
2. OpenAPI 3.1 contract (`contracts/openapi.yaml`) — **done**
3. Gherkin acceptance stubs + `pytest-bdd` skeletons (failing)
4. Implementation: `crypto.py` → `db/` → `adapters.py` → `routes/` → SPA
5. Refactor under green tests
