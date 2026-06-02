# Tasks: Datasink Export Pipeline

**Input**: Design documents from `specs/changes/002-datasink-export/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/openapi.yaml ✅ | quickstart.md ✅

**Tests**: Included — Constitution §III (TDD) is NON-NEGOTIABLE. Failing test stubs MUST be committed before any implementation task in the same phase.

**Organization**: Tasks grouped by user story. Each phase is independently testable and deliverable.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story label — [US1], [US2], [US3]
- Exact file paths in every description

## User Story Map

| Label | Story | Priority |
|-------|-------|----------|
| US1 | Export Data to Datasink | P1 — MVP |
| US2 | Monitor Export Jobs | P2 |
| US3 | Asset Resolution on Export | P3 |

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Runtime dependencies, package skeleton, worker entrypoint

- [X] T001 Add `arq>=0.26` and `redis[hiredis]>=5.0` to `[project].dependencies` in `pyproject.toml`; add `fakeredis>=2.20` to `[dependency-groups.dev]`; run `uv sync`
- [X] T002 Add `redis` service (image `redis:7-alpine`, port 6379, healthcheck) to `docker-compose.dev.yml`
- [X] T003 [P] Create `src/databridge/sinks/__init__.py` (empty) and `src/databridge/sinks/base.py` (empty class stub `class BaseSink: pass`)
- [X] T004 [P] Create `src/databridge/export/__init__.py` (empty) and `worker/__main__.py` (stub: `if __name__ == "__main__": pass`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure required by ALL user stories — config, auth, metrics, DB, models

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T005 Add `DatasinkConfig` frozen dataclass (name, type, url, path, filename_template) and `ExportSettings` frozen dataclass (all 7 tunables from data-model.md §1.2) to `src/databridge/config.py`; extend `Settings` with `datasinks: tuple[DatasinkConfig, ...]` and `export: ExportSettings`; update `get_settings()` loader with strict key validation for `datasinks` and `export` YAML sections; update `_SETTINGS_VALID_KEYS`
- [X] T006 Extend `src/databridge/auth.py`: add `org_id: str = ""`, `user_id: str = ""`, `role: str = "user"` to `AuthContext` NamedTuple (defaults preserve backwards compatibility); update `get_auth()` to split `X-Group-ID` on first `/` into `org_id`/`user_id`; map `X-Role` header (`SUPER_ADMIN` / `ORG_ADMIN` / `USER`) to `role`; debug fallback: org_id="dev", user_id="dev", role="super_admin"
- [X] T007 [P] Create `src/databridge/export_metrics.py` with all 8 Prometheus instruments from data-model.md §10: `export_jobs_created_total` (Counter, labels: org_id, sink_type), `export_jobs_completed_total`, `export_jobs_failed_total`, `export_active_jobs` (Gauge, label: org_id), `export_records_per_second` (Gauge, label: sink_type), `export_asset_resolution_success_total` (Counter), `export_asset_resolution_failed_total` (Counter), `export_org_concurrent_jobs` (Gauge, label: org_id)
- [X] T008 Create Alembic migration script in `src/databridge/db/migrations/`: create `export_jobs` table with all 20 columns and 4 indexes as defined in data-model.md §5.1; run `uv run alembic upgrade head` to apply
- [X] T009 Create `src/databridge/export/models.py`: `FilterSnapshot`, `ExportJobStatus` (Enum), `ExportJob`, `ExportJobCreate`, `ExportJobResponse`, `ExportJobListResponse`, `DatasinkInfo`, `DatasinkDatasetListResponse`, `AssetFieldDetectRequest`, `AssetFieldDetectResponse` — all schemas from data-model.md §3–4
- [X] T010 Create `src/databridge/export/db.py`: `insert_export_job`, `get_export_job`, `list_export_jobs` (role-filtered with org_id/user_id/role, paginated), `update_export_job_status`, `update_export_progress` (records_processed, records_skipped, asset_errors, last_heartbeat_at), `count_active_jobs_for_org` (returns count of pending+running by org_id under pg_advisory_xact_lock)
- [X] T011 [P] Write failing unit test stubs in `tests/unit/test_config_export.py`: DatasinkConfig parses all 4 types from YAML; ExportSettings applies correct defaults; unknown datasink key raises ValueError; missing `url` for service sink raises ValueError; missing `path` for local sink raises ValueError
- [X] T012 [P] Write failing unit test stubs in `tests/unit/test_auth_roles.py`: X-Group-ID `acme/alice` → org_id="acme", user_id="alice"; X-Role SUPER_ADMIN → role="super_admin", is_org_admin=True; X-Role ORG_ADMIN → role="org_admin", is_org_admin=True; absent X-Role → role="user", is_org_admin=False; backwards compat: `public_key` field on AuthContext (legacy field used by existing datasource routes) still equals the full raw `X-Group-ID` header value to avoid breaking existing code; debug fallback when server.debug=true

**Checkpoint**: Foundation ready — user story implementation can begin. Confirm `uv run pytest tests/unit/test_config_export.py tests/unit/test_auth_roles.py` reports failures (not errors) before proceeding.

---

## Phase 3: User Story 1 — Export Data to Datasink (Priority: P1) 🎯 MVP

**Goal**: User selects a datasink in the inline Export block, presses Export, a job is created, the ARQ worker processes it in batches, and progress is visible by polling GET /api/v1/export-jobs/{id}.

**Independent Test**: Start Redis + API + worker; open a datasource; scroll to Export block; select any YAML-configured datasink; enter a dataset name; press Export. Verify: job row appears in GET /api/v1/export-jobs with status "pending" → "running" → "completed"; records_processed increments; GET /api/v1/datasinks returns the configured sinks; GET /api/v1/datasinks/{name}/datasets returns available datasets.

### Failing Tests — Write FIRST (before any T016+ implementation)

- [X] T013 [P] [US1] Write failing unit test stubs in `tests/unit/test_sinks.py`: BaseSink is abstract (cannot instantiate); DatasetMockSink.ping() calls GET {url}/health; DatasetMockSink.list_datasets() calls GET {url}/datasets; DatasetMockSink.post_file() calls POST {url}/datasets/{dataset}/files; AnnotatorMockSink.list_datasets() calls GET {url}/api/v1/projects; AnnotatorMockSink.post_file() calls POST {url}/api/v1/projects/{dataset}/tasks; LocalZipSink.post_file() writes JSON into ZIP; LocalZipSink filename falls back to content hash when template field absent; LocalJsonlSink.post_file() skips non-serializable records; get_sink() raises ValueError for unknown type
- [X] T014 [P] [US1] Write failing integration test stubs in `tests/integration/test_export_jobs_create.py`: POST /api/v1/export-jobs returns 201 with ExportJobResponse; returned job has status="pending", records_total=null; GET /api/v1/export-jobs/{id} returns 200; unknown datasink_name returns 400; org over concurrent limit returns 429 with informative message
- [X] T015 [P] [US1] Write failing integration test stubs in `tests/integration/test_datasinks.py`: GET /api/v1/datasinks returns configured sinks; GET /api/v1/datasinks/{name}/datasets returns list; unknown sink name returns 404; unreachable datasink returns 502

### Implementation

- [X] T016 [US1] Implement `src/databridge/sinks/base.py`: `BaseSink` ABC with `ping()`, `list_datasets()`, `create_dataset(name)`, `post_file(dataset, record, filename)`, `finalise()` — all `@abstractmethod`; `__init__(self, config: DatasinkConfig)`
- [X] T017 [P] [US1] Implement `src/databridge/sinks/dataset_mock.py`: `DatasetMockSink(BaseSink)` — all 5 methods using httpx.AsyncClient against `config.url`; `finalise()` is no-op; ignore 409 on create_dataset
- [X] T018 [P] [US1] Implement `src/databridge/sinks/annotator_mock.py`: `AnnotatorMockSink(BaseSink)` — adapter layer: list_datasets → GET /api/v1/projects; create_dataset → POST /api/v1/projects (ignore 409 — project may already exist); post_file → POST /api/v1/projects/{dataset}/tasks; ping → GET /health; finalise is no-op
- [X] T019 [P] [US1] Implement `src/databridge/sinks/local_zip.py`: `LocalZipSink(BaseSink)` — ping checks dir writable; create_dataset opens `zipfile.ZipFile` in memory; post_file resolves `config.filename_template` fields from record dict (`{field}` substitution), falls back to `hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:16]`; finalise closes and writes ZIP to `config.path/{dataset}_{job_id}.zip`; catches OSError on disk full
- [X] T020 [P] [US1] Implement `src/databridge/sinks/local_jsonl.py`: `LocalJsonlSink(BaseSink)` — ping checks dir writable; create_dataset opens file handle; post_file attempts `json.dumps(record)` + newline append, silently skips and increments local skip counter if TypeError/ValueError; finalise flushes and closes file; skip count accessible as `.records_skipped` property for worker to sum into job counters
- [X] T021 [US1] Complete `src/databridge/sinks/__init__.py`: `_SINK_REGISTRY` dict and `get_sink(config: DatasinkConfig) -> BaseSink` factory (registry lookup, raise ValueError for unknown type)
- [X] T022 [US1] Add `ExportableAdapter` Protocol + `count()` / `fetch_page()` implementations to `src/databridge/adapters.py`: ClickHouseConnectionAdapter (COUNT(*) SQL + LIMIT/OFFSET), TrinoConnectionAdapter (COUNT(*) via statement API + LIMIT/OFFSET), LangfuseConnectionAdapter (meta.total from list endpoint + page-based pagination), S3ConnectionAdapter (DuckDB COUNT(*) + LIMIT/OFFSET via asyncio.to_thread); `BaseAdapter.count()` raises NotImplementedError
- [X] T023 [US1] Create `src/databridge/export/worker.py`: `run_export_job(ctx, job_id)` ARQ coroutine — load job from DB; mark running; call `adapter.count()`; update records_total; `sink.create_dataset(destination_dataset)`; batch loop (fetch_page → post_file per record → update_export_progress every batch + refresh last_heartbeat_at); emit keep-alive update when batch exceeds keepalive_interval; `sink.finalise()`; mark completed/failed; update EXPORT_JOBS_COMPLETED/FAILED metrics, EXPORT_ACTIVE_JOBS gauge; `WorkerSettings` class referencing `run_export_job` and `redis_settings` from config; all exceptions caught, job marked failed with error_message
- [X] T024 [US1] Implement `worker/__main__.py`: load Settings; build ARQ `RedisSettings` from `settings.export.redis_url`; call `arq.run_worker(WorkerSettings)` so `python -m worker` launches the ARQ worker process
- [X] T025 [US1] Create `src/databridge/routes/datasinks.py`: `GET /api/v1/datasinks` returns DatasinkListResponse from settings; `GET /api/v1/datasinks/{name}/datasets` fetches DatasinkConfig by name (404 if missing), instantiates sink via get_sink(), calls `sink.list_datasets()` (502 on connection error), returns DatasinkDatasetListResponse; both require get_auth() dependency
- [X] T026 [US1] Create `src/databridge/routes/export_jobs.py`: `POST /api/v1/export-jobs` — validate datasink_name exists in config (400 if not); call count_active_jobs_for_org (429 if ≥ limit); insert_export_job; enqueue ARQ job via arq pool with job_id=str(export_job_id); increment EXPORT_JOBS_CREATED counter; return 201 ExportJobResponse; `GET /api/v1/export-jobs/{id}` — get_export_job (404 if not found or not visible per role); return ExportJobResponse
- [X] T027 [US1] Register new routes in `src/databridge/main.py`: import and include `datasinks_router` (prefix `/api/v1`) and `export_jobs_router` (prefix `/api/v1`); add ARQ pool creation to `lifespan()` and store as `app.state.arq_pool`; add `get_arq_pool()` dependency to `src/databridge/routes/deps.py`
- [X] T028 [US1] Add Export & Destination block to `src/databridge/templates/browser.html`: `data-testid="export-block"` section below Data Preview; `#datasink-select` (select element); `#destination-dataset-select` (datalist-backed input); `#asset-resolution-toggle` (checkbox); `#export-btn` (button); all elements hidden until a datasource is selected; Jobs nav tab `#jobs-tab`
- [X] T029 [US1] Add export block JS logic to `src/databridge/static/browser.js`: on datasource select → show export block, call `GET /api/v1/datasinks` to populate `#datasink-select`; on datasink change → call `GET /api/v1/datasinks/{name}/datasets` to populate `#destination-dataset-select` datalist; on `#export-btn` click → POST `/api/v1/export-jobs` with current datasource ref + filter snapshot + datasink + dataset name; on 201 → show success toast + navigate to Jobs tab; on 400/429 → show error message

**Checkpoint**: US1 fully functional. Verify: `uv run pytest tests/integration/test_export_jobs_create.py tests/integration/test_datasinks.py tests/unit/test_sinks.py` green. Start Redis + worker + API, perform end-to-end export via UI and confirm job reaches "completed".

---

## Phase 4: User Story 2 — Monitor Export Jobs (Priority: P2)

**Goal**: Users see all their jobs (role-filtered) in a dedicated Jobs tab with status, progress, timestamps, and a Retry button for failed jobs. Stale jobs auto-fail after 15 minutes. Old jobs auto-purge after 7 days.

**Independent Test**: Trigger an export; open Jobs tab; confirm job row has correct source, sink, status badge, and progress counter; poll updates without page reload; trigger retry on a seeded failed job → new job appears at top with same settings; seed a `super_admin` user and verify they see all jobs; seed an `org_admin` and verify they see only their org's jobs.

### Failing Tests — Write FIRST

- [X] T030 [P] [US2] Write failing integration test stubs in `tests/integration/test_export_jobs_list.py`: GET /api/v1/export-jobs with user role returns only caller's jobs; org_admin role returns all org jobs; super_admin returns all; pagination: page=1&page_size=2 returns 2 items; status filter works; POST /api/v1/export-jobs/{id}/retry on failed job returns 201 new job with same settings; retry on non-failed job returns 400; retry when at concurrent limit returns 429

### Implementation

- [X] T031 [US2] Create `src/databridge/export/sweep.py`: `mark_stale_jobs(pool, timeout_minutes)` — UPDATE export_jobs SET status='failed', error_message='...' WHERE status='running' AND last_heartbeat_at < NOW() - INTERVAL; `ttl_purge_jobs(pool, ttl_days)` — DELETE FROM export_jobs WHERE status IN ('completed','failed') AND completed_at < NOW() - INTERVAL; `run_sweep_loop(pool, settings)` — asyncio infinite loop calling both functions every 60 seconds
- [X] T032 [US2] Update `worker/__main__.py`: after ARQ worker starts, launch `run_sweep_loop` as a background asyncio task; pass shared asyncpg pool + export settings
- [X] T033 [US2] Extend `src/databridge/routes/export_jobs.py`: add `GET /api/v1/export-jobs` — role-filtered list via `list_export_jobs(pool, auth, page, page_size, status_filter)`, return ExportJobListResponse; add `POST /api/v1/export-jobs/{id}/retry` — load job (404/role check); assert status=failed (400 if not); check per-org limit (429 if hit); clone job settings into new ExportJobCreate; insert + enqueue; return 201 with new job
- [X] T034 [US2] Add Jobs tab content to `src/databridge/templates/browser.html`: `#jobs-list` container div inside the `#jobs-tab` panel; tab switching logic via `#jobs-tab` nav button; empty-state message when no jobs
- [X] T035 [US2] Add jobs list JS to `src/databridge/static/browser.js`: `loadJobs()` calls `GET /api/v1/export-jobs`; render each job as `#job-row-{id}` with `#job-status-{id}` badge, `#job-progress-{id}` text ("N exported" or "N / M (X%)"), `#job-source-{id}`, `#job-sink-{id}`, `#job-retry-btn-{id}` (visible only when status=failed); `startJobsPolling()` sets 3-second interval calling `loadJobs()` while any job is pending/running; `#job-retry-btn-{id}` click → POST `/api/v1/export-jobs/{id}/retry` → reload list

**Checkpoint**: US2 fully functional. Verify: `uv run pytest tests/integration/test_export_jobs_list.py` green. Confirm Jobs tab shows live progress updates and retry creates a new job row.

---

## Phase 5: User Story 3 — Asset Resolution on Export (Priority: P3)

**Goal**: Users enable "Resolve Assets", select an asset datasink, review auto-detected URL fields, optionally set a prefix, and the worker fetches + stores assets during export; records with failing asset fetches are skipped and counted.

**Independent Test**: Export data with known asset URL fields enabled; confirm assets appear in the target asset dataset; confirm skipped-record count is non-zero when an asset URL is broken; confirm asset dataset name is auto-derived as `{destination_dataset}_assets` in read-only UI label.

### Failing Tests — Write FIRST

- [X] T036 [P] [US3] Write failing unit test stubs in `tests/unit/test_asset.py`: `detect_asset_url_fields` returns "image_url" for schema with field named "image_url"; returns "file_url" when example value matches `^https?://`; does NOT return fields without URL indicators; `resolve_assets` with prefix prepends to field value before fetch; `resolve_assets` returns updated record with stored asset path when fetch+post succeed; `resolve_assets` raises `AssetResolutionError` on 404 (caller skips record); prefix="" means field value used as-is
- [X] T037 [P] [US3] Write failing integration test stubs in `tests/integration/test_datasinks_asset.py`: POST /api/v1/datasinks/{name}/detect-asset-fields with connection_id returns candidate_fields; POST with system_source_name returns candidate_fields; POST with neither → 400; unknown connection_id → 404

### Implementation

- [X] T038 [US3] Create `src/databridge/export/asset.py`: `detect_asset_url_fields(schema: dict[str, SchemaField], sample_records: list[dict]) -> list[str]` — name convention check (field leaf segment in URL_FIELD_NAMES set) OR URL regex on example/sample values; `resolve_assets(record: dict, url_fields: list[str], url_prefix: str, asset_sink: BaseSink, asset_dataset: str) -> dict` — for each url_field: prepend prefix; fetch binary via httpx; call `asset_sink.post_file(asset_dataset, ...)` with binary content; raise `AssetResolutionError` on 4xx/timeout/connection error; return record with field values replaced by stored asset references
- [X] T039 [US3] Extend `src/databridge/routes/datasinks.py`: add `POST /api/v1/datasinks/{name}/detect-asset-fields` — validate exactly one of connection_id/system_source_name provided (400); load connection or system source (404); call `adapter.schema()` to get schema fields; call `adapter.preview(..., limit=20)` for sample records; call `detect_asset_url_fields(schema, samples)`; return AssetFieldDetectResponse
- [X] T040 [US3] Integrate asset resolution into `src/databridge/export/worker.py`: when `job.asset_resolution=True` — instantiate asset_sink via get_sink(asset_datasink_config); call `asset_sink.create_dataset(job.asset_dataset)` before loop; in per-record loop: call `resolve_assets(record, ...)`; on `AssetResolutionError` → skip record, increment asset_errors counter, increment EXPORT_ASSET_RESOLUTION_FAILED metric; on success → increment EXPORT_ASSET_RESOLUTION_SUCCESS metric; if asset_sink.ping() fails at start → fail job with clear error
- [X] T041 [US3] Add asset resolution UI elements to `src/databridge/templates/browser.html`: `#asset-datasink-select`, `#asset-dataset-label` (read-only input), `#asset-url-fields-list` (div for checkboxes), `#asset-url-prefix-input` (text input); all wrapped in a collapsible `#asset-resolution-config` section toggled by `#asset-resolution-toggle`
- [X] T042 [US3] Add asset resolution JS to `src/databridge/static/browser.js`: show/hide `#asset-resolution-config` on `#asset-resolution-toggle` change; on show — call GET /api/v1/datasinks for `#asset-datasink-select`; call POST /api/v1/datasinks/{name}/detect-asset-fields with current datasource ref to populate `#asset-url-fields-list` checkboxes (`#asset-url-field-{name}`, all pre-checked); compute and display `{destination_dataset}_assets` in `#asset-dataset-label` reactively when destination input changes; include asset_resolution fields in POST /api/v1/export-jobs payload

**Checkpoint**: US3 fully functional. Verify: `uv run pytest tests/unit/test_asset.py tests/integration/test_datasinks_asset.py` green. Run end-to-end asset export and confirm asset dataset populated in annotator-mock or dataset-mock.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: E2E coverage, performance validation, observability verification, spec synchronisation

- [X] T043 [P] Write and run E2E test in `tests/e2e/test_export_flow.py` (Playwright): open datasource page → scroll to `#export-block` → select datasink → enter dataset name → click `#export-btn` → switch to `#jobs-tab` → wait for `#job-status-{id}` to show "completed" using `getByTestId()`; verify `#job-progress-{id}` shows final count; verify retry flow on a seeded failed job
- [X] T044 Run full integration suite: `uv run pytest tests/integration/ -v`; ensure all export job and datasink tests pass; fix any regressions in existing connection tests caused by auth.py changes
- [X] T045 [P] Run full unit suite: `uv run pytest tests/unit/ -v`; ensure test_config_export.py, test_auth_roles.py, test_sinks.py, test_asset.py all pass; ensure existing tests (test_crypto, test_adapters, test_security, test_auth) still pass
- [X] T046 [P] Performance validation: export 10,000 records from a local ClickHouse (seeded via dev/init-clickhouse.sql) to `local-jsonl` sink; assert wall-clock time < 2 minutes (SC-006); if >2 min investigate batch_size tuning; document result in `specs/changes/002-datasink-export/research.md` Decision 11
- [X] T047 [P] Metrics validation: run an export; hit `GET /metrics`; assert all 8 `export_*` metric names appear with correct labels; assert `export_jobs_created_total{sink_type="local-jsonl"}` incremented
- [X] T048 Update `specs/current/export-pipeline.md` with the implemented pipeline contract: datasink types, job status transitions, role-based visibility, stale timeout, TTL, metrics signal list
- [X] T049 Run `quickstart.md` validation end-to-end: follow all steps in `specs/changes/002-datasink-export/quickstart.md`; confirm Redis, worker, API start cleanly; execute every curl example; verify expected responses; fix any discrepancies in code or quickstart doc
- [X] T050 [P] API load test (Constitution §VI): run `wrk -t4 -c50 -d30s` (or equivalent) against `POST /api/v1/export-jobs` and `GET /api/v1/export-jobs` with 50 concurrent connections; assert p95 ≤500 ms for both endpoints; document result in `specs/changes/002-datasink-export/research.md` Decision 12; if p95 exceeds limit, investigate DB query plan on `export_jobs` indexes

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup)
  └── Phase 2 (Foundational)  ← BLOCKS all user stories
        ├── Phase 3 (US1)     ← MVP — can ship here
        │     └── Phase 4 (US2) ← extends US1 (jobs list, retry, sweep)
        │           └── Phase 5 (US3) ← extends US1 worker (asset loop)
        └── Phase 6 (Polish)  ← after all desired stories complete
```

### User Story Dependencies

| Story | Depends On | Can Parallelise With |
|-------|-----------|---------------------|
| US1 (P1) | Phase 2 complete | — |
| US2 (P2) | Phase 2 complete + US1 routes/DB exist (T026, T010) | — |
| US3 (P3) | Phase 2 complete + US1 sinks + US1 worker (T023) | — |

### Within Each Story (fixed order)

1. **Failing tests** committed first (Constitution §III)
2. **Models/protocols** (BaseSink, ExportableAdapter)
3. **Services** (sink implementations, worker task, sweep)
4. **Routes** (API endpoints)
5. **UI** (browser.html + browser.js)
6. **Checkpoint validation**

### Parallel Opportunities

- T003 ∥ T004 (different packages)
- T007 ∥ T011 ∥ T012 (different files, no deps)
- T013 ∥ T014 ∥ T015 (failing test stubs, different files)
- T017 ∥ T018 ∥ T019 ∥ T020 (separate sink files)
- T030 ∥ T036 ∥ T037 (failing test stubs, different files)
- T043 ∥ T044 ∥ T045 ∥ T046 ∥ T047 ∥ T050 (independent validation)

---

## Parallel Example: User Story 1

```bash
# Step 1 — Write failing tests in parallel (all land in different files):
Task T013: tests/unit/test_sinks.py
Task T014: tests/integration/test_export_jobs_create.py
Task T015: tests/integration/test_datasinks.py

# Step 2 — Implement sink classes in parallel (different files):
Task T017: src/databridge/sinks/dataset_mock.py
Task T018: src/databridge/sinks/annotator_mock.py
Task T019: src/databridge/sinks/local_zip.py
Task T020: src/databridge/sinks/local_jsonl.py
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (~1 hour)
2. Complete Phase 2: Foundational — CRITICAL, blocks everything (~3 hours)
3. Complete Phase 3: User Story 1 (~6 hours)
4. **STOP and VALIDATE**: `pytest tests/unit/test_sinks.py tests/integration/test_export_jobs_create.py tests/integration/test_datasinks.py`; manual end-to-end export via UI
5. **SHIP MVP** — Users can export data; no job list, no retry, no asset resolution yet

### Incremental Delivery

1. Setup + Foundational → Foundation ready (T001–T012)
2. US1 → Export works, single job visible via GET /:id (T013–T029) — **Demo/deploy**
3. US2 → Jobs tab live, retry, stale sweep, TTL purge (T030–T035) — **Demo/deploy**
4. US3 → Asset resolution end-to-end (T036–T042) — **Demo/deploy**
5. Polish → E2E, perf, metrics, spec sync, API load test (T043–T050)

---

## Notes

- **[P]** = different source files, safe to run simultaneously
- **[USn]** = maps task to user story for traceability and independent shipment
- Constitution §III: always commit failing tests before running the matching implementation task
- Constitution §VII: model → contract → failing tests → implementation → refactor
- Each Checkpoint must pass before the next phase starts
- `auth.py` change (T006) touches existing routes — run `uv run pytest tests/unit/test_auth.py tests/integration/` after T006 to catch regressions before proceeding
- Worker runs as `python -m worker`; API runs as `uvicorn databridge.main:app`; both need Redis reachable
- `PROMETHEUS_MULTIPROC_DIR` must be set when running API + worker together (shared metrics scrape)
