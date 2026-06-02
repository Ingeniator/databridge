# Implementation Plan: Datasink Export Pipeline

**Branch**: `001-datasink-export` | **Date**: 2026-06-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/changes/002-datasink-export/spec.md`

## Summary

Add a full export pipeline to databridge: users select a configured datasink, optionally enable asset resolution, press Export, and a background ARQ/Redis worker fetches records from the datasource in paginated batches and writes them to the chosen sink. Four sink types are supported out of the box (dataset-mock, annotator-mock, local ZIP, local JSONL). Export jobs are tracked in PostgreSQL, progress is polled by the UI at 3-second intervals, and role-based visibility (user / org_admin / super_admin) is enforced via the existing X-Group-ID + X-Role header pair. All sink types extend an abstract `BaseSink`; adding a new sink requires only a new subclass and a registry entry.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: FastAPI 0.135+, uvicorn, asyncpg, cryptography (Fernet), alembic, pyyaml, httpx, aioboto3, duckdb, structlog, prometheus-client, jinja2 — plus new: `arq>=0.26`, `redis[hiredis]>=5.0`

**Storage**: PostgreSQL — new `export_jobs` table with JSONB for filter snapshot and asset URL fields; Alembic migration; datasinks loaded from YAML config (no DB table)

**Testing**: pytest, pytest-asyncio (strict mode), respx (HTTP mocking for outbound datasink service calls only — dataset-mock and annotator-mock are external services; real PostgreSQL is always used in integration tests), moto[s3], pytest-bdd (Gherkin stubs), playwright + pytest-playwright (E2E)

**Target Platform**: Linux server / docker-compose (same environment as ai-suite)

**Project Type**: Web service — FastAPI REST API + vanilla-JS SPA + ARQ background worker (separate process)

**Performance Goals**: API endpoints p95 ≤ 500 ms; 10,000-record JSONL export completes within 2 minutes; worker batch processing at ≥100 records/batch with configurable batch size

**Constraints**: All adapter I/O async; DuckDB/S3 blocking calls via `asyncio.to_thread`; no synchronous calls in FastAPI event loop; Redis-backed ARQ queue required (synchronous dev fallback only when `debug=true`); PROMETHEUS_MULTIPROC_DIR required for multi-process metrics; the existing `PrometheusMiddleware` auto-instruments all routes with request counters and latency histograms, satisfying Constitution §VI for the 4 new API routes without per-route setup

**Scale/Scope**: Per-org concurrent job cap (default 5); jobs auto-purged after 7-day TTL; stale running jobs auto-failed after 15-minute timeout

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| Model exists | ✅ | `data-model.md` created in Phase 1 — Pydantic models, DB schema, BaseSink ABC, metrics, UI testid map |
| Contract exists | ✅ | `contracts/openapi.yaml` created in Phase 1 — 6 new endpoints with full request/response schemas and examples |
| Tests first | ✅ | Failing test stubs committed before implementation per Constitution §III |
| Test IDs | ✅ | All new UI elements carry `data-testid`; spec notation in `data-model.md` §11 |
| Async I/O | ✅ | All adapter methods async; S3+DuckDB via `asyncio.to_thread`; ARQ worker is fully async |
| Metrics | ✅ | 8 new Prometheus instruments (counters + gauges) for export pipeline; defined in `data-model.md` §10 |
| Performance | ✅ | 10k-record JSONL target: 2 min (SC-006); p95 ≤ 500 ms for all API endpoints |

No violations — Complexity Tracking table omitted.

## Project Structure

### Documentation (this feature)

```text
specs/changes/002-datasink-export/
├── plan.md              ← this file
├── research.md          ← Phase 0 decisions (10 decisions)
├── data-model.md        ← Phase 1: Pydantic models + DB schema + BaseSink ABC + UI testids
├── quickstart.md        ← Phase 1: local dev setup with Redis + worker
├── contracts/
│   └── openapi.yaml     ← Phase 1: 6 new endpoints (datasinks + export-jobs)
└── tasks.md             ← Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/databridge/
├── config.py                    # + DatasinkConfig frozen dataclass; ExportSettings dataclass;
│                                #   Settings extended with datasinks + export fields
│                                #   strict key validation for datasinks section
├── auth.py                      # AuthContext extended: org_id, user_id, role fields
│                                #   (backwards-compatible NamedTuple defaults)
│                                #   X-Group-ID split into org_id/user_id; X-Role → role
├── export_metrics.py            # 8 new Prometheus Counters + Gauges for export pipeline
│                                #   (separate module to avoid circular imports with worker)
│
├── sinks/
│   ├── __init__.py              # get_sink() factory + _SINK_REGISTRY
│   ├── base.py                  # BaseSink ABC: ping, list_datasets, create_dataset,
│   │                            #   post_file, finalise
│   ├── dataset_mock.py          # DatasetMockSink — native unified protocol
│   ├── annotator_mock.py        # AnnotatorMockSink — adapter layer over native API
│   ├── local_zip.py             # LocalZipSink — zipfile, filename template, hash fallback
│   └── local_jsonl.py           # LocalJsonlSink — json.dumps per record, skip non-serializable
│
├── export/
│   ├── __init__.py
│   ├── models.py                # FilterSnapshot, ExportJob, ExportJobCreate,
│   │                            #   ExportJobResponse, ExportJobListResponse, etc.
│   ├── db.py                    # insert_export_job, get_export_job, list_export_jobs,
│   │                            #   update_export_job_status, update_export_progress,
│   │                            #   count_active_jobs_for_org
│   ├── worker.py                # run_export_job() ARQ task; WorkerSettings class;
│   │                            #   batch loop with keep-alive; finalise + status update
│   ├── sweep.py                 # mark_stale_jobs() (15-min timeout sweep every 60 s)
│   │                            #   ttl_purge_jobs() (7-day TTL purge)
│   └── asset.py                 # detect_asset_url_fields(); resolve_assets()
│                                #   fetch asset by URL, post to asset sink, return updated record
│
├── adapters.py                  # + ExportableAdapter protocol (count + fetch_page)
│                                #   + count() / fetch_page() on CH, Trino, Langfuse, S3 adapters
│
├── routes/
│   ├── datasinks.py             # GET /api/v1/datasinks
│   │                            # GET /api/v1/datasinks/{name}/datasets
│   │                            # POST /api/v1/datasinks/{name}/detect-asset-fields
│   └── export_jobs.py           # POST /api/v1/export-jobs (create + enqueue)
│                                # GET  /api/v1/export-jobs (list, role-filtered, paginated)
│                                # GET  /api/v1/export-jobs/{id}
│                                # POST /api/v1/export-jobs/{id}/retry
│
├── db/
│   └── migrations/              # new Alembic version: add export_jobs table + indexes
│
├── templates/
│   └── browser.html             # + Jobs tab in main nav (#jobs-tab)
│                                # + Export & Destination block below Data Preview (#export-block)
│
└── static/
    └── browser.js               # + export block rendering, datasink select, asset resolution toggle
                                 # + jobs tab: list, status badges, progress display, retry button
                                 # + 3-second polling loop for active jobs

worker/
└── __main__.py                  # python -m worker → arq.run_worker(WorkerSettings)

tests/
├── unit/
│   ├── test_sinks.py            # BaseSink subclass behaviour, registry dispatch
│   ├── test_export_worker.py    # batch loop, keep-alive, stale sweep, TTL purge
│   └── test_asset.py            # detect_asset_url_fields, resolve_assets, prefix prepend
├── integration/
│   ├── test_export_jobs.py      # create/list/get/retry via real PG + respx for sinks
│   └── test_datasinks.py        # /datasinks + /detect-asset-fields endpoints
└── e2e/
    └── test_export_flow.py      # Playwright: open datasource → export → jobs tab → verify
```

**Structure Decision**: Same FastAPI web service layout as Phase 1. Worker is a separate Python process (no new container definition in pyproject.toml — entry point is `worker/__main__.py`). Sinks live in `src/databridge/sinks/` (new package). Export business logic lives in `src/databridge/export/` (new package). Both follow the single-responsibility principle (Constitution §IV) — each module has one reason to change.

## Architecture Sequence (per Constitution §VII)

```
data-model.md  →  contracts/openapi.yaml  →  failing test stubs  →  implementation  →  refactor
```

1. Pydantic models + DB schema + BaseSink ABC (`data-model.md`) — **done**
2. OpenAPI 3.1 contract (`contracts/openapi.yaml`) — **done**
3. Gherkin acceptance stubs + `pytest-bdd` skeletons (failing) — Phase 2 (tasks.md)
4. Implementation: `config.py` ext → `export_metrics.py` → `sinks/` → `export/` → `routes/` → SPA
5. Refactor under green tests

## Key Design Decisions (from research.md)

| # | Decision | Rationale |
|---|---|---|
| 1 | ARQ + Redis for worker IPC | Constitution §VI mandate; ARQ supports heartbeats and job metadata natively |
| 2 | AuthContext extended with org_id/user_id/role | Backwards-compatible; X-Group-ID `org/user` split + X-Role header for 3 roles |
| 3 | `datasinks:` YAML section (analogous to `datasources:`) | Consistent config shape; ops teams know this pattern from Phase 1 |
| 4 | ExportableAdapter protocol (count + fetch_page) | Separates preview interface (capped, UI) from export interface (full iteration) |
| 5 | Stale sweep as background asyncio task in worker (60s poll) | Observable, survives Redis/ARQ restarts; simpler than a dedicated sweep service |
| 6 | Asset URL detection: name convention + URL regex on schema/samples | Pre-selects candidates; user confirms — no silent data loss |
| 7 | Per-org concurrency check via PostgreSQL COUNT + advisory lock | Atomic, no Redis round-trip; aligns with existing DB-first pattern |
| 8 | Local sink paths from DatasinkConfig.path (shared volume) | Ops-configurable; no hardcoded paths; fails clearly if path unwritable |
| 9 | Annotator-mock via internal adapter layer (no protocol change) | Sink interface stable; adapter maps native API calls — O/C principle |
| 10 | export_metrics.py separate module | Avoids circular imports between worker.py and metrics.py |
