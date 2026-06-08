# Tasks: Browser UI Redesign

**Input**: Design documents from `specs/changes/003-browser-ui-redesign/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/openapi-delta.yaml ✅ | quickstart.md ✅

**Tests**: Included — constitution §III mandates TDD (NON-NEGOTIABLE). Failing tests MUST be committed before implementation commits.

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no shared dependencies)
- **[Story]**: User story this task belongs to (US1/US2/US3)
- All tasks include exact file paths

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dev environment ready for new test tooling

- [X] T001 [P] Add `respx` to pyproject.toml dev dependencies for outbound webhook unit tests
- [X] T002 [P] Add `pytest-playwright` and `playwright` to pyproject.toml dev dependencies; run `uv run playwright install chromium`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: DB migration + updated Pydantic models + CSS base tokens — MUST be complete before any user story work

**⚠️ CRITICAL**: No user story implementation can begin until this phase is complete

- [X] T003 Write Alembic migration `0003_export_job_enhancements.py` in `src/databridge/db/migrations/versions/0003_export_job_enhancements.py`: `ADD COLUMN masking_rules JSONB NOT NULL DEFAULT '[]'`, `ADD COLUMN sampling_config JSONB`, `ADD COLUMN webhook_url TEXT`, `ADD COLUMN webhook_enabled BOOLEAN NOT NULL DEFAULT FALSE`; include `downgrade()` that drops all four columns
- [X] T004 Update `FilterSnapshot` (add `time_field: str | None` and `limit: int = Field(default=50, ge=1, le=100_000)`) and `PreviewResponse` (add `total_count: int`) in `src/databridge/export/models.py`
- [X] T005 Add `MaskingAction` enum, `MaskingRule` model, `SamplingMethod` enum, `SamplingConfig` model, and `PiiFieldsResponse` model to `src/databridge/export/models.py`
- [X] T006 Update `ExportJobCreate`, `ExportJob`, and `ExportJobResponse` in `src/databridge/export/models.py` to include `masking_rules: list[MaskingRule]`, `sampling_config: SamplingConfig | None`, `webhook_url: str | None`, `webhook_enabled: bool`
- [X] T007 Add CSS custom properties `--color-primary: #094cb2`, `--color-primary-fixed: #d9e2ff`, `--color-tertiary-container: #bfab49` under `:root` in `src/databridge/static/browser.css`; add `font-label` declaration loading Public Sans 400/600 from the existing Google Fonts CDN link

**Checkpoint**: Migration applies cleanly (`uv run alembic upgrade head`), models import without error, CSS tokens resolve in browser. Foundation ready — user story phases can now proceed.

---

## Phase 3: User Story 1 — Browse & Filter Data from a Datasource (Priority: P1) 🎯 MVP

**Goal**: Connection tab bar with health status, schema discovery, time range + predicate filter with structured builder, data preview table with colored status badges, CLEAR ALL, and Load More — all backed by a `total_count`-aware `/preview` endpoint.

**Independent Test**: Load the browser page, select a connection tab, apply `status == 'error'` in the predicate filter, confirm matching rows appear in the Data Preview table, confirm total row count updates, click CLEAR ALL, confirm rows reset.

### Tests for User Story 1 (TDD — write first, confirm failure, then implement)

- [X] T008 [P] [US1] Write failing unit test for `total_count` returned in preview response (assert `response.total_count >= 0`, `response.results` length ≤ `limit`) in `tests/unit/test_preview.py`
- [X] T009 [P] [US1] Write failing integration test for `POST /api/v1/connections/{id}/preview` with `FilterSnapshot` containing `time_field` and `limit`; assert `total_count` and `schema_fields` present in response in `tests/integration/test_connections.py`
- [X] T010 [US1] Write failing Playwright E2E stubs (skipped/xfail) for: connection tab click → schema chip appears (`#schema-chip-*`), predicate filter input (`#predicate-filter-input`) updates preview table (`#preview-table`), CLEAR ALL button (`#clear-all-btn`) resets rows, Load More button (`#load-more-btn`) appends rows — in `tests/e2e/test_browser_redesign.py`

### Implementation for User Story 1

- [X] T011 [US1] Update `POST /api/v1/connections/{id}/preview` in `src/databridge/routes/connections.py` to: run `COUNT(*)` in parallel with data fetch via `asyncio.gather`; pass `filter.time_field` to the adapter's filter clause; return `PreviewResponse` with `total_count` and `schema_fields`
- [X] T012 [US1] Rewrite `src/databridge/templates/browser.html`: `TopNav` with `data-testid="nav-tab-import"` and `data-testid="nav-tab-jobs"` tabs; `ConnectionTabBar` with `data-testid="connection-tab-bar"`; `RefineDatasetCard` with `data-testid="refine-dataset-card"`, `SchemaDiscoverySection`, `FilterRow` (TimeRangeColumn + PredicateColumn + AdvancedFilterPanel); `DataPreviewSection` with `data-testid="data-preview-section"`, `PreviewTable`, `LoadMoreBtn`; `ConnModal` (existing logic, new `data-testid` attributes per data-model.md §7); all placeholders for US2 cards and `JobsView` as empty `<div>` stubs with correct `data-testid`
- [X] T013 [US1] Rewrite `src/databridge/static/browser.js`: declare module-scope state (`_connections`, `_activeId`, `_schema`, `_filterState`, `_previewRows`, `_previewLimit`, `_totalCount`, `_maskingRules`, `_samplingConfig`, `_webhookConfig`, `_assetResolution: false`, `_assetUrlFields: []`, `_assetUrlPrefix: ''`, `_assetDatasinkName: null`, `_visibleColumns: null`); implement `renderConnectionTabBar()` (includes `AddConnectionTab` entry at end of tab list with `data-testid="add-connection-tab"` click handler that opens ConnModal — FR-003), `renderSchemaSection()`, `renderPreviewTable()` (filters displayed columns against `_visibleColumns` when set; status badge coloring for PROCESSED/ERROR/PENDING/RUNNING fields), `renderColumnPicker()` (dropdown toggled by `#visibility-btn`/`#columns-picker-btn`; updates `_visibleColumns` Set and re-renders table), `selectConnection()` refactor (auto-triggers schema detect), `loadPreview()` that calls `POST /preview` and updates `_totalCount`
- [X] T014 [US1] Add to `src/databridge/static/browser.js`: `updateClearAllVisibility()` (watches `_filterState`), time range select handler (`data-testid="time-range-select"`) — render as `disabled` when `_schema` has no timestamp-type field per FR-007, time field badge click handler, `loadMoreRows()` (doubles `_previewLimit`, re-fetches), `AdvancedFilterPanel` builder (add/remove rule rows, AND/OR toggle when ≥2 rules, syncs to `_filterState.query`), `renderAdvancedFilterPanel()`, `validatePredicate(expr)` guard that displays inline error below `#predicate-filter-input` and suppresses the preview call when the expression is syntactically invalid
- [X] T015 [US1] Add to `src/databridge/static/browser.css`: connection tab styles (`.conn-tab`, `.conn-tab--active` with `--color-primary` underline and bold label); schema section styles (`.schema-section`, `.field-chip`, `.type-badge-pill`); health badge states (`.health-badge--healthy`, `.health-badge--syncing`, `.health-badge--error`); filter row layout; data preview table styles; status badge pill variants (`.status-badge`, `.status-badge--processed`, `.status-badge--error`, `.status-badge--pending`)

**Checkpoint**: User Story 1 independently functional. Run `uv run pytest tests/unit/test_preview.py tests/integration/test_connections.py -v` — all pass. Run `uv run pytest tests/e2e/test_browser_redesign.py -k "tab or schema or filter or preview" --headed` — E2E stubs turn green.

---

## Phase 4: User Story 2 — Configure and Launch a Data Export (Priority: P2)

**Goal**: Data Masking card with PII auto-detection, Sampling Strategy card, Export & Destination section with prominent Export button, Webhook Configuration card — all backed by new `masking.py`, `sampling.py`, `webhook.py` modules wired into the ARQ worker and DB.

**Independent Test**: Select a datasink, enable masking (add one rule), set sampling ratio 0.10, enter a webhook URL, click Export, confirm job appears in Jobs tab with PENDING status.

### Tests for User Story 2 (TDD — write first, confirm failure, then implement)

- [X] T016 [P] [US2] Write failing unit tests for `apply_masking()` in `tests/unit/test_masking.py`: test each `MaskingAction` (mask→`***`, hash→SHA-256 hex, drop→field absent, redact→`[REDACTED]`); test `pii_candidate_fields()` heuristic returns fields containing `email`, `phone`, `user_id`, `ip_address`; test nested dot-path fields
- [X] T017 [P] [US2] Write failing unit tests for `SamplingBuffer` in `tests/unit/test_sampling.py`: random strategy preserves ~N% of records; systematic emits every Nth; stratified maintains subgroup proportions; edge cases: empty input, ratio=1.0 returns all, ratio>1.0 treated as absolute count
- [X] T018 [US2] Write failing integration test in `tests/integration/test_export_jobs.py`: POST `/export-jobs` with `masking_rules=[{field_path:"payload.user_id",action:"mask"}]`, `sampling_config={method:"random",ratio_or_size:0.1}`, `webhook_url:"http://test"`, `webhook_enabled:false`; assert four new DB columns stored and retrieved via GET `/export-jobs/{id}`; assert worker applies masking and sampling (verify output record count ≈ 10% of input, verify `payload.user_id` is `***`)

### Implementation for User Story 2

- [X] T019 [P] [US2] Create `src/databridge/export/masking.py`: `apply_masking(record: dict, rules: list[MaskingRule]) -> dict` — iterate rules, resolve dot-path, apply action; `pii_candidate_fields(schema_fields: dict) -> list[str]` — return keys matching PII name patterns (`email`, `phone`, `ssn`, `password`, `ip`, `user_id`, `token`, `secret`, `card`)
- [X] T020 [P] [US2] Create `src/databridge/export/sampling.py`: `SamplingBuffer` class with `method: SamplingMethod`, `target_column: str | None`, `ratio_or_size: float`; `feed(record: dict) -> bool` returns True if record kept; implements random (reservoir), systematic (every Nth), stratified (per-group quota) strategies
- [X] T021 [P] [US2] Create `src/databridge/export/webhook.py`: `async deliver_webhook(url: str, payload: dict) -> None` — POST via `httpx.AsyncClient` with 10 s timeout; log success/failure via `structlog`; no retry; no exception propagation (fire-and-forget)
- [X] T022 [US2] Update `src/databridge/export/db.py`: extend `insert_export_job`, `get_export_job`, and `list_export_jobs` to read and write `masking_rules` (JSONB↔list[dict]), `sampling_config` (JSONB↔dict|None), `webhook_url` (TEXT|None), `webhook_enabled` (BOOL)
- [X] T023 [US2] Update `src/databridge/export/worker.py`: when `job.masking_rules` is non-empty, call `apply_masking(record, rules)` per record before writing to sink; when `job.sampling_config` is set, wrap record feed through `SamplingBuffer` and skip dropped records; after `finalize()`, if `job.webhook_enabled and job.webhook_url`, call `deliver_webhook(url, completion_payload)` as a background task
- [X] T024 [US2] Update `src/databridge/routes/export_jobs.py`: pass `masking_rules`, `sampling_config`, `webhook_url`, `webhook_enabled` from request body through to `insert_export_job`; include them in retry handler (copy from original job)
- [X] T025 [US2] Add `GET /api/v1/connections/{id}/pii-fields` endpoint to `src/databridge/routes/connections.py`: load schema for the connection, call `pii_candidate_fields()` from `masking` module, return `PiiFieldsResponse`
- [X] T026 [US2] Add to `src/databridge/export_metrics.py`: three worker business counters — `masking_rules_applied_total` (labels: `org_id`), `sampling_records_dropped_total` (labels: `org_id`), `webhook_delivery_total` (labels: `org_id`, `status` in `success`/`failure`); two route latency histograms — `pii_fields_request_duration_seconds` Histogram (labels: `connection_type`) and `preview_request_duration_seconds` Histogram (labels: `connection_type`); instrument `GET /pii-fields` handler in `routes/connections.py` and verify `POST /preview` handler is instrumented (add if missing) — constitution §VI MUST
- [X] T027 [US2] Add `DataMaskingCard`, `SamplingStrategyCard`, `ExportDestinationSection`, and `WebhookConfigCard` HTML to `src/databridge/templates/browser.html` (insert between `DataPreviewSection` and the US3 `JobsView` stub), all with `data-testid` attributes per data-model.md §7
- [X] T028 [US2] Add to `src/databridge/static/browser.js`: `renderDataMaskingCard()` (toggle, rules table, add-row button); PII auto-detect handler (fetches `/pii-fields`, appends candidate rows to masking table); `renderSamplingCard()` (toggle, method select with description update, target column, ratio input)
- [X] T029 [US2] Add to `src/databridge/static/browser.js`: Export button click handler that serialises `_filterState`, `_maskingRules`, `_samplingConfig`, `_webhookConfig`, `_assetResolution`, `_assetUrlFields`, `_assetUrlPrefix`, `_assetDatasinkName` into `ExportJobCreate` body and POSTs to `/api/v1/export-jobs`, then switches to Jobs tab; `AssetResolutionToggle` handler (wires `#asset-resolution-toggle`, updates `_assetResolution` and shows/hides asset URL fields); Test Webhook button handler that POSTs to `/api/v1/export-jobs/test-webhook` and shows success/error toast
- [X] T030 [US2] Add to `src/databridge/static/browser.css`: masking card styles (`.masking-card`, `.masking-rule-row`); sampling card styles (`.sampling-card`); export section styles (`.export-section`, Export button using `--color-primary`); webhook card styles (`.webhook-card`)

**Checkpoint**: Full export flow functional. Run `uv run pytest tests/unit/test_masking.py tests/unit/test_sampling.py tests/integration/test_export_jobs.py -v` — all pass.

---

## Phase 5: User Story 3 — Monitor Export Jobs (Priority: P3)

**Goal**: Jobs tab listing export jobs with colored status badges (running/completed/failed), record counts, timestamps, and a Retry action for failed jobs — polling every 3 seconds.

**Independent Test**: Trigger an export job, navigate to the Jobs tab, confirm the job row appears with PENDING→RUNNING→COMPLETED status transitions within 10 seconds; trigger a failing job, confirm Retry button appears and clicking it creates a new job.

### Tests for User Story 3 (TDD — write first, confirm failure, then implement)

- [X] T031 [P] [US3] Write failing integration tests in `tests/integration/test_export_jobs.py`: assert `GET /api/v1/export-jobs` response includes `masking_rules`, `sampling_config`, `webhook_url`, `webhook_enabled` fields per job; assert `POST /api/v1/export-jobs/{id}/retry` creates a new job with same masking/sampling/webhook config
- [X] T032 [US3] Write failing Playwright E2E stubs in `tests/e2e/test_browser_redesign.py` for: clicking `#nav-tab-jobs` shows `#jobs-view`; after export job created, `#job-row-{id}` appears with `#job-status-{id}` badge; status transitions to COMPLETED within 5 s (`expect(page.getByTestId('job-status-' + id)).toHaveText('COMPLETED', { timeout: 5000 })` — SC-005); `#job-retry-btn-{id}` visible on failed job and creates new job row when clicked

### Implementation for User Story 3

- [X] T033 [US3] Add `JobsView` HTML section to `src/databridge/templates/browser.html` (replace the US1-phase stub): `data-testid="jobs-view"` wrapper, `data-testid="jobs-empty-msg"` empty state, job row template with `data-testid="job-row-{id}"`, status badge `data-testid="job-status-{id}"`, source/sink/progress/download/retry elements all per data-model.md §7
- [X] T034 [US3] Add `renderJobsView()` to `src/databridge/static/browser.js`: map job status → badge color class (`running`→blue, `completed`→green, `failed`→red, `pending`→amber); render download button only for local-sink jobs; render retry button only for failed jobs; wiring to 3-second polling interval (reuse existing `_jobPollTimer` pattern)
- [X] T035 [US3] Add to `src/databridge/static/browser.css`: job row styles (`.job-row`); three new job-status badge variants (`.status-badge--running`, `.status-badge--completed`, `.status-badge--failed`) — `.status-badge--pending` is already defined in T015 (Phase 3) and must NOT be redefined here; pulsing dot animation (`.dot-pulse` keyframe for RUNNING/SYNCING indicators)

**Checkpoint**: Jobs tab functional with live status updates. Run `uv run pytest tests/integration/test_export_jobs.py -v` and E2E jobs stubs — all pass.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Full E2E coverage, accessibility, performance validation, spec alignment

- [ ] T036 Run the full Playwright E2E test suite and fix all failures: `uv run pytest tests/e2e/test_browser_redesign.py --headed -v` — all acceptance scenarios from spec.md User Stories 1–3 must pass
- [X] T037 [P] Audit `src/databridge/templates/browser.html` for `data-testid` completeness: verify every element in data-model.md §7 (50+ entries) has the correct `data-testid` attribute; add any missing attributes
- [X] T038 [P] Add keyboard event handlers in `src/databridge/static/browser.js` for all interactive controls (tab key navigation through conn tabs, Enter to trigger filter, Escape to close modal/panel) to satisfy SC-004 (all controls reachable via keyboard alone)
- [ ] T039 Load test `POST /api/v1/connections/{id}/preview` with 50 concurrent requests using `locust`; confirm p95 ≤ 500 ms per SC-001; document result in `specs/changes/003-browser-ui-redesign/research.md` under a new "Performance Validation" section
- [ ] T040 Execute the quickstart.md verification checklist end-to-end: run migration, start service + worker, verify connection tab bar, schema discovery, predicate filter, masking, sampling, export with webhook, jobs tab status transitions, and local-sink download
- [X] T041 [P] Update `specs/current/` to merge this feature's changes per constitution §VIII: create or update `specs/current/export-pipeline.md` to reflect masking/sampling/webhook steps; update `specs/current/asset-resolution.md` if needed

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately; T001 and T002 can run in parallel
- **Foundational (Phase 2)**: Depends on Phase 1 completion — BLOCKS all user story phases; T003–T006 are sequential (same file); T007 independent
- **User Story Phases (3–5)**: All depend on Phase 2 completion; can proceed in story-priority order (P1→P2→P3) or in parallel if staffed
- **Polish (Phase 6)**: Depends on all user story phases complete

### User Story Dependencies

- **US1 (P1)**: Can start after Phase 2 — no dependency on US2 or US3
- **US2 (P2)**: Can start after Phase 2 — uses `_filterState` from US1 but does not require US1 UI complete; worker/backend tasks (T019–T026) are independent of US1 frontend tasks
- **US3 (P3)**: Can start after Phase 2 — `renderJobsView()` is independent; requires export job infrastructure from US2 backend (T022–T024) to be complete for full E2E testing

### Within Each User Story

1. Write failing tests first (TDD — commit before implementation)
2. Backend/model tasks before route tasks
3. Route tasks before frontend tasks
4. Core JS logic before CSS

### Parallel Opportunities

- T001 ‖ T002 (Phase 1)
- T007 ‖ any other Phase 2 task (different file)
- T008 ‖ T009 (different test files, US1)
- T016 ‖ T017 (different test files, US2)
- T019 ‖ T020 ‖ T021 (different new modules, US2)
- T031 ‖ T032 is partially parallel (different files, US3)
- T037 ‖ T038 ‖ T041 (Polish)

---

## Parallel Examples

### User Story 1: Testing sprint

```
Task T008: tests/unit/test_preview.py       ← parallel
Task T009: tests/integration/test_connections.py  ← parallel
```

### User Story 2: New module sprint

```
Task T019: export/masking.py    ← parallel
Task T020: export/sampling.py   ← parallel
Task T021: export/webhook.py    ← parallel
```

---

## Implementation Strategy

### MVP (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (migration + models + CSS tokens)
3. Complete Phase 3: User Story 1 (preview, tab bar, schema, filter, data table)
4. **STOP and validate**: run unit + integration + E2E for US1
5. Demo/deploy: data exploration fully functional

### Incremental Delivery

1. Foundation → US1 → validate → deploy (data browsing MVP)
2. Add US2 backend modules → validate → deploy (exports with masking/sampling/webhook)
3. Add US3 jobs view → validate → deploy (observability complete)
4. Polish → full E2E + load test → ship

### Parallel Team Strategy

After Phase 2 completes:
- Developer A: US1 frontend (T012–T015) + US1 route (T011)
- Developer B: US2 backend modules (T019–T026)
- Developer C: US3 jobs view (T033–T035) after US2 backend

---

## Notes

- `[P]` = different files, no shared incomplete dependencies — safe to parallelise
- `[Story]` maps task to user story for traceability against spec.md FR-001–FR-019
- TDD is non-negotiable (constitution §III): failing test commit MUST precede implementation commit in git log
- All new UI elements MUST carry `data-testid` per data-model.md §7; Playwright tests MUST use `getByTestId()`
- No blocking I/O: all new async adapter/route calls MUST use `async`/`await`
- Commit after each logical group; use checkpoint instructions to validate incrementally
