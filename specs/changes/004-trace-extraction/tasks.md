# Tasks: Field Extraction Stage

**Input**: Design documents from `specs/changes/004-trace-extraction/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/openapi.yaml ✅ | quickstart.md ✅

**Tests**: Included — Constitution §III (TDD) is NON-NEGOTIABLE. Failing test stubs MUST be committed before the implementation task(s) that make them pass, within the same phase.

**Organization**: Tasks grouped by user story. Each phase is independently testable and deliverable.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story label — [US1], [US2], [US3]
- Exact file paths in every description

## User Story Map

| Label | Story | Priority |
|-------|-------|----------|
| US1 | Export the nested value directly instead of the raw envelope | P1 — MVP |
| US2 | Masking still protects extracted content | P2 |
| US3 | Visibility into extraction outcomes | P3 |

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Skeleton files for the new module and migration

- [X] T001 [P] Create `src/databridge/export/extraction.py` with only a module docstring (no functions yet) — new per-record transform module, sibling of `masking.py`/`asset.py`/`sampling.py`
- [X] T002 [P] Create `src/databridge/db/migrations/versions/0008_field_extraction.py` with `revision = "0008"`, `down_revision = "0007"`, and empty `upgrade()`/`downgrade()` bodies (`pass`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The extraction mechanism, DB columns, and models shared by every user story — nothing in Phase 3+ is testable without this

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 [P] Write failing unit test stubs in `tests/unit/test_extraction.py`: `resolve_field_path` returns the value for a single-segment path; descends multi-segment dotted paths through nested dicts; transparently `json.loads`s a string container encountered mid-path (e.g. an `event_properties` field stored as a JSON-encoded string); returns a "not found" sentinel when any path segment is absent or the container isn't a dict; `extract_field_value` returns a native `dict`/`list` value unchanged; returns the parsed object when the resolved value is a JSON-encoded string that decodes to a `dict`/`list`; returns `None` for a missing field, an empty string, a plain non-JSON string, and a JSON-encoded bare scalar (e.g. `"123"`, `"true"`)
- [X] T004 Implement `resolve_field_path(container, parts)` and `extract_field_value(record, field_path)` in `src/databridge/export/extraction.py` per data-model.md §2, to make T003 pass (depends on T003, T001). Also added `_decode_and_index` single-segment primitive and list-index support (`items.0.email`) — discovered during implementation that `masking._apply_at_path` had grown list-index support since data-model.md was written, so the shared helper needed to match that surface for T005's DRY refactor not to regress it; covered by 3 extra tests.
- [X] T005 Refactor `_apply_at_path` in `src/databridge/export/masking.py` to call `extraction._decode_and_index` for its single-segment descend step instead of its own inline JSON-string-transparent/list-index dispatch, keeping only the mutate-in-place + re-serialize wrapper local to masking (Constitution §IV DRY, research.md Decision 3); `uv run pytest tests/unit/test_masking.py` — 15 passed, zero regressions (depends on T004)
- [X] T006 Add `field_extraction: bool = False` and `field_extraction_path: str = ""` fields to `ExportJob`, `ExportJobCreate`, `ExportJobResponse` in `src/databridge/export/models.py`; add a `model_validator(mode="after")` on `ExportJobCreate` that raises `ValueError("field_extraction_path is required when field_extraction is enabled")` when `field_extraction` is true and `field_extraction_path.strip()` is empty; add `FieldExtractionTestRequest`, `FieldExtractionTestResult`, `FieldExtractionTestResponse` models per data-model.md §1.3
- [X] T007 [P] Fill in `src/databridge/db/migrations/versions/0008_field_extraction.py`: `upgrade()` runs `ALTER TABLE export_jobs ADD COLUMN field_extraction BOOLEAN NOT NULL DEFAULT FALSE` and `ALTER TABLE export_jobs ADD COLUMN field_extraction_path TEXT NOT NULL DEFAULT ''`; `downgrade()` runs matching `DROP COLUMN IF EXISTS` statements; verified via `uv run alembic history` (0008 now head, chain intact). Actually applied later (T025): rebuilding/restarting the `ai-suite-databridge` container ran `alembic upgrade head` (0007→0008) against the real dev DB on startup — log-confirmed (depends on T002)
- [X] T008 Update `src/databridge/export/db.py`: read `row["field_extraction"]` / `row["field_extraction_path"]` into `_row_to_response`'s `ExportJobResponse(...)` construction; add `data.field_extraction` and `data.field_extraction_path` as two more columns + `$n` placeholders + positional params in `insert_export_job`'s `INSERT INTO export_jobs` statement (depends on T006, T007). `uv run pytest tests/unit/test_export_db.py` — 44 passed.
- [X] T009 [P] Add `EXPORT_FIELD_EXTRACTION_SUCCESS` and `EXPORT_FIELD_EXTRACTION_FAILED` unlabeled `Counter` instruments to `src/databridge/export_metrics.py`, mirroring `EXPORT_ASSET_RESOLUTION_SUCCESS`/`_FAILED`

**Checkpoint**: Foundation ready. Confirm `uv run pytest tests/unit/test_extraction.py tests/unit/test_masking.py` is green before starting user story work.

---

## Phase 3: User Story 1 — Export the nested value directly instead of the raw envelope (Priority: P1) 🎯 MVP

**Goal**: An operator enables field extraction on an export job, points it at a nested field path, runs the job, and the output dataset contains the extracted values instead of the raw envelopes. Records lacking usable content at that path are skipped and counted; jobs with the feature off behave exactly as before.

**Independent Test**: Configure an export job against a source where records contain a nested field holding structured content, enable field extraction with that field's path, run the job, and confirm the destination dataset contains the extracted values as standalone records rather than full envelopes, with skipped records reflected in `records_skipped`.

### Failing Tests — Write FIRST (before T013+)

- [X] T010 [P] [US1] Write failing unit test stubs in `tests/unit/test_export_worker.py`: when `field_extraction=True` and `field_extraction_path` resolves to usable content for a record, the per-record loop in `run_export_job` replaces `record` with the extracted value before it reaches `sink.post_file` (assert on the payload passed to a stubbed/mocked sink); when the path doesn't resolve or resolves to unusable content, the record is dropped without raising, `records_skipped` increments, and `EXPORT_FIELD_EXTRACTION_FAILED` increments; when `field_extraction=False` (default), records pass through unchanged (regression check against current behavior). Also updated the shared `_job_row()` test fixture with `field_extraction`/`field_extraction_path` defaults so all pre-existing worker tests keep working once the code reads those keys.
- [X] T011 [P] [US1] Write failing unit test stubs in `tests/unit/test_export_jobs_routes.py`: `POST /api/v1/export-jobs` with `field_extraction=true` and `field_extraction_path` omitted/empty returns `422` with a validation error mentioning `field_extraction_path`; `field_extraction=true` with a non-empty path returns `201` and the response body echoes both new fields; `field_extraction=false` (the default) requires no `field_extraction_path` and is accepted. These passed immediately (4/4) since T006's validator already covered the contract — no implementation gap.
- [X] T012 [P] [US1] Write failing unit test stubs for `POST /connections/{id}/test-field-extraction` in **`tests/unit/test_connections_routes.py`** (deviated from the originally planned `tests/integration/test_datasinks_extraction.py` path — discovered while implementing that this exact class of single-connection-scoped `/connections/{id}/test-*` endpoint already has its established test home in `test_connections_routes.py`, e.g. `test_asset_resolution_*`, using a lightweight mocked-router `client` fixture rather than the full-app config-file integration style used for `/datasinks/{name}/*` endpoints): resolving path → `resolved: true` + `value_preview`; non-resolving path → `resolved: false` + `error`; unknown connection id → `404`; adapter preview failure → `502`
- [X] T013 [US1] Wire the extraction stage into `src/databridge/export/worker.py`'s per-record loop in `run_export_job`: read `job_resp["field_extraction"]` / `job_resp["field_extraction_path"]` before the batch loop; insert the new stage between the sampling check and the `if masking_rules:` block — on missing/unusable content, `records_skipped += 1`, `EXPORT_FIELD_EXTRACTION_FAILED.inc()`, `continue`; on success, reassign `record = extracted`, `EXPORT_FIELD_EXTRACTION_SUCCESS.inc()` (depends on T004, T006, T008, T009; makes T010 pass). `uv run pytest tests/unit/test_export_worker.py` — 15 passed.
- [X] T014 [US1] Add `POST /connections/{id}/test-field-extraction` route to `src/databridge/routes/connections.py`, resolving the adapter via the same system-source-or-DB-connection branch used by `test_asset_resolution`, calling `adapter.preview("", None, None, limit=5)`, then `extraction.extract_field_value` per sample record, returning a `FieldExtractionTestResponse` (depends on T004, T006; makes T012 pass). `uv run pytest tests/unit/test_connections_routes.py` — 44 passed.
- [X] T015 [US1] Add a Field Extraction block to `src/databridge/templates/browser.html`, as its own card (mirroring the Webhook Configuration card's structure rather than squeezing into the Asset Resolution/Select Sink 2-column grid, since a 3rd item there would require restructuring an existing layout for no benefit): checkbox with `data-testid="field-extraction-toggle"`, text input with `data-testid="field-extraction-path-input"`, button with `data-testid="test-field-extraction-btn"`, results panel with `data-testid="field-extraction-results"` and per-row `data-testid="field-extraction-result-{n}"` (hidden until the toggle is checked)
- [X] T016 [US1] Wire the new block in `src/databridge/static/browser.js`: `onFieldExtractionToggle`/`onFieldExtractionPathChange`/`testFieldExtraction` (same show/hide + test-call pattern as `onAssetResolutionToggle`/`testAssetResolution`); registered `field-extraction-toggle` in the enable-on-datasource-select list and in `window.DB`; included `field_extraction`/`field_extraction_path` in the `POST /api/v1/export-jobs` payload object (depends on T014). Verified with `node --check` (no Python test coverage for browser.js in this repo).

**Checkpoint**: US1 fully functional and independently testable — this is the MVP. Verified: `uv run pytest tests/unit/test_export_worker.py tests/unit/test_export_jobs_routes.py tests/unit/test_connections_routes.py` all green (full `tests/unit` suite: 420 passed). Manual browser walkthrough of quickstart.md steps 2–3 not yet done — flagged for the polish/verify pass.

---

## Phase 4: User Story 2 — Masking still protects extracted content (Priority: P2)

**Goal**: Masking rules configured on a job continue to correctly redact sensitive fields once field extraction is also enabled, because extraction runs before masking.

**Independent Test**: Configure a job with both field extraction and a masking rule targeting a field that exists within the extracted content, run it, and confirm the field is masked in the output.

### Failing Tests — Write FIRST

- [X] T017 [P] [US2] Write failing unit test stubs in `tests/unit/test_export_worker.py`: a job with both `field_extraction=True` and a `masking_rules` entry targeting a field that exists only inside the extracted content correctly masks that field in the record passed to `sink.post_file`; a masking rule targeting a field that existed only in the original envelope (absent from the extracted content) causes no error and has no effect (written alongside T010, in the same file)

### Implementation

- [X] T018 [US2] No new production code needed — the stage order delivered by T013 (extraction runs before the `if masking_rules:` block) made T017 pass as-is. Confirmed: `uv run pytest tests/unit/test_export_worker.py -k masking` — 2 passed.

**Checkpoint**: US1 and US2 both work independently.

---

## Phase 5: User Story 3 — Visibility into extraction outcomes (Priority: P3)

**Goal**: An operator monitoring a job can see how many records were extracted versus skipped, via the same skip-count and metrics reporting the export pipeline already provides.

**Independent Test**: Run a job against a source where some records have the configured field and others don't, and confirm the reported skipped count matches the number of records lacking usable content at that path.

### Failing Tests — Write FIRST

- [X] T019 [P] [US3] Split across two files based on what's actually testable without live services:
  - `tests/integration/test_export_jobs.py` (fake-pool style, no real DB/Redis needed): `field_extraction`/`field_extraction_path` round-trip through `POST /export-jobs` → response; both fields present on every item in `GET /export-jobs`; `GET /export-jobs/{id}` surfaces `records_skipped`/`records_processed` (simulating what a worker run would have written)
  - `tests/unit/test_export_worker.py::test_field_extraction_metrics_increment`: runs `run_export_job` with one resolving + one non-resolving record, asserts `EXPORT_FIELD_EXTRACTION_SUCCESS`/`EXPORT_FIELD_EXTRACTION_FAILED` (`prometheus_client` Counter `._value.get()`) incremented by exactly 1 each

  **Regression found and fixed while writing these**: `_MaskingPool._make_job` in `test_export_jobs.py` has a positional signature that mirrors `insert_export_job`'s `INSERT INTO export_jobs` column list; T008 added 2 columns, so this fake broke for every test in the file (not just the new ones) — fixed by adding `field_extraction, field_extraction_path` params. Separately discovered `masking_client`'s fixture never mocked the ARQ pool (unlike `test_datasinks_asset.py`'s fixture), so all 5 pre-existing tests in this file were already failing in this sandbox (confirmed via `git stash` on a clean checkout) — fixed by adding the same `patch("arq.create_pool", ...)` mock already used elsewhere, which was necessary to verify my new tests pass at all. `uv run pytest tests/integration/test_export_jobs.py` — 8 passed (5 pre-existing + 3 new).

### Implementation

- [X] T020 [US3] No new production code needed — skip-counting and metrics increments were delivered by T013. Confirmed: `uv run pytest tests/unit/test_export_worker.py tests/integration/test_export_jobs.py -q` — 16 + 8 passed.

**Checkpoint**: All 3 user stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Constitution §VIII merge-back (pipeline order is a documented contract) and final regression sweep

- [X] T021 [P] Update the "Worker Record Loop" section of `specs/current/export-pipeline.md` to `sampling → field extraction → masking → assets → sink`, and add a brief "Field Extraction" section summarizing the feature, matching the existing "Data Masking"/"Asset Resolution" sections' style and length. Also added the new endpoint row to the API Endpoints table.
- [X] T022 [P] Create `specs/current/field-extraction.md` implemented-contract doc mirroring `specs/current/asset-resolution.md`'s structure (Overview, extraction function signature, worker integration conditions, API endpoint, metrics, constraints)
- [X] T023 [P] Add `export_field_extraction_success_total` / `export_field_extraction_failed_total` rows to the Metrics table in `specs/current/export-pipeline.md`
- [X] T024 Full regression: targeted files — `uv run pytest tests/unit/test_masking.py tests/unit/test_extraction.py tests/unit/test_export_worker.py tests/unit/test_export_jobs_routes.py tests/unit/test_connections_routes.py tests/integration/test_export_jobs.py` — 157 passed. Whole-suite sweep — `uv run pytest tests/unit tests/integration` — 506 passed, 4 failed, 14 errored; verified via `git stash` on a clean checkout that all 18 non-passing tests fail identically with zero relation to this branch (missing ARQ pool mocks in `test_export_jobs_create.py`/`test_export_jobs_list.py`, and `test_export_worker_e2e.py` needing live Postgres/ClickHouse/MinIO not reachable in this sandbox). Zero regressions introduced.
- [X] T025 Rebuilt and restarted the `ai-suite-databridge` container from this working tree (`docker compose build databridge && docker compose up -d --no-deps databridge` from `/Users/mironov/Projects/ai-suite`). Verified live at `http://localhost:8888/databridge/`: migration 0007→0008 ran on startup (log-confirmed); `/openapi.json` shows `field_extraction`/`field_extraction_path` on `ExportJobCreate`/`ExportJobResponse` and the `test-field-extraction` path; served HTML contains all `field-extraction-*` data-testids; `POST /api/v1/export-jobs` with `field_extraction=true` and no path returns `422` live, matching the unit-tested validator. Did not click through the UI in an actual browser (no browser automation available here) — API/asset-level verification substitutes for that; a human visual pass is still worthwhile.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup (T001, T002) — BLOCKS all user stories
- **User Stories (Phase 3–5)**: All depend on Foundational phase completion
  - US1 (Phase 3) has no dependency on US2/US3
  - US2 (Phase 4) and US3 (Phase 5) both depend on T013 (US1's worker wiring) existing, since they verify behavior of that same stage rather than adding new production code — sequence them after US1 in practice, even though they don't touch US1's files
- **Polish (Phase 6)**: Depends on all three user stories being complete

### Within Each Phase

- Tests MUST be written and FAIL before their corresponding implementation task (Constitution §III)
- T004 depends on T003; T005 depends on T004; T008 depends on T006 and T007
- T013 depends on T004, T006, T008, T009; T014 depends on T004, T006

### Parallel Opportunities

- T001, T002 (Setup) run in parallel
- T003 (Foundational tests) can start once T001 exists; T007 and T009 can run in parallel with T003–T006 (different files)
- T010, T011, T012 (US1 tests) run in parallel — three different files
- T015 and T016 are sequential (T016 wires up what T015 renders) but both can start once T013/T014 land
- T021, T022, T023 (Polish docs) run in parallel — three different files/sections

---

## Parallel Example: User Story 1

```bash
# Launch all failing tests for User Story 1 together:
Task: "Failing unit tests for worker extraction stage in tests/unit/test_export_worker.py"
Task: "Failing validator tests in tests/unit/test_export_jobs_routes.py"
Task: "Failing integration tests for test-field-extraction endpoint in tests/integration/test_datasinks_extraction.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: run the Phase 3 checkpoint commands, walk quickstart.md steps 2–3
5. Deploy/demo if ready — this alone delivers the full "extract instead of raw envelope" value

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. Add US1 → test independently → deploy/demo (MVP!)
3. Add US2 → test independently (verification-only, no new prod code expected) → deploy/demo
4. Add US3 → test independently (verification-only, no new prod code expected) → deploy/demo
5. Polish → merge pipeline-order contract update back into `specs/current/`

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- US2 and US3 are deliberately thin (P2/P3, low implementation weight per spec) — their value is proving FR-008/FR-010 hold, not adding code; if either fails, the fix lands in `worker.py` (T013), not in a new task
- Commit after each task or logical group; failing-test commit MUST precede its implementation commit in git log (Constitution §III)
- Verify tests fail before implementing
- Stop at any checkpoint to validate a story independently
