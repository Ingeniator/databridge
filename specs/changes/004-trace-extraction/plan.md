# Implementation Plan: Field Extraction Stage

**Branch**: `003-trace-extraction` | **Date**: 2026-07-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/changes/004-trace-extraction/spec.md`

## Summary

Add an opt-in `field_extraction` stage to the export pipeline. When enabled on a job, each fetched record is reduced to the value found at a single configured `field_extraction_path` (e.g. `event_properties.trace` for an Amplitude-style trace payload, but any nested JSON value is a valid target), transparently descending through JSON-encoded string containers along the way, exactly as masking's dotted-path resolution already does. Only structured (JSON-parseable) values count as usable content; anything else (missing field, plain non-JSON string, empty value) causes the record to be skipped and counted in `records_skipped`. The stage runs immediately after sampling and before masking, so masking rules continue to protect whatever content actually reaches the sink. This mirrors the existing `asset_resolution` feature in shape (a single boolean + field config on the job, not a rule list) but is architecturally distinct from masking rules because it replaces the record rather than mutating a field in place.

**Naming note**: this feature was initially scoped and prototyped as "trace extraction," but the mechanism is generic — it extracts whatever JSON value lives at a nested path, of which a trace payload is one example, not the definition of scope. Renamed to `field_extraction` during spec clarification (2026-07-13) to match this codebase's naming convention for other opt-in per-record stages (`masking`, `sampling`, `asset_resolution` — short, generic nouns, no use-case baked in). All identifiers below use the new name; the git branch (`003-trace-extraction`) and spec-directory name (`004-trace-extraction`) retain the original name since renaming them mid-flight isn't worth the churn.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: FastAPI, asyncpg, alembic, pydantic, structlog, prometheus-client — no new third-party dependencies required.

**Storage**: PostgreSQL — two new columns on the existing `export_jobs` table (`field_extraction BOOLEAN`, `field_extraction_path TEXT`), added via Alembic migration `0008`. No new tables.

**Testing**: pytest / pytest-asyncio, following the existing split — unit tests for the extraction function and worker stage logic (`tests/unit/`), integration tests for the new preview endpoint and job-creation validation against real PostgreSQL (`tests/integration/`).

**Target Platform**: Same FastAPI web service + ARQ worker process as the rest of the export pipeline.

**Project Type**: Web service — extension of an existing pipeline stage, no new services.

**Performance Goals**: Negligible overhead — extraction is a single dict/string traversal per record, same cost class as the existing masking field-path resolution it reuses. No change to the existing p95 ≤ 500 ms API target or the 10k-record / 2-minute export target.

**Constraints**: Extraction MUST run before masking (Constitution §VIII — pipeline step order is a behavioral contract; this is a declared order change, not an incidental one). Must not introduce synchronous I/O into the worker's per-record loop — extraction is pure in-memory parsing, no network calls.

**Scale/Scope**: Exactly one field path per job (not a list) — matches the spec's "single key" requirement and keeps this a sibling of `asset_resolution`, not an extension of `masking_rules`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| Model exists | ✅ | `data-model.md` (Phase 1) — Pydantic field additions, migration, new request/response models |
| Contract exists | ✅ | `contracts/openapi.yaml` (Phase 1) — extended `POST /api/v1/export-jobs` schema + new `POST /api/v1/connections/{id}/test-field-extraction` |
| Tests first | ⚠ Pending | Failing test stubs must precede implementation commits (enforced at `/speckit-tasks` + `/speckit-implement`, not at planning time) |
| Test IDs | ✅ | New UI elements get `data-testid`; mapped in `data-model.md` |
| Async I/O | ✅ | Extraction itself is synchronous in-memory parsing inside the already-async worker loop, consistent with how masking runs today; the new preview endpoint reuses the existing async `adapter.preview()` path |
| Metrics | ✅ | Two new unlabeled counters, mirroring `EXPORT_ASSET_RESOLUTION_SUCCESS`/`FAILED` |
| Performance | ✅ | No new perf goal needed — overhead is the same order as existing masking traversal, already covered by the 10k-record/2-min export SC |
| Pipeline order is contract (§VIII) | ⚠ Requires doc update | `specs/current/export-pipeline.md`'s "Worker Record Loop" section declares the current order; this feature changes it (`sampling → masking → assets` becomes `sampling → field extraction → masking → assets`). Since the stage is opt-in and off by default, no existing job's behavior changes — but the order change itself still needs the spec update + an acceptance test asserting order, per the constitution's own escape hatch for additive, non-breaking order changes. Tracked as an implementation task, not deferred. |

No unjustified violations — Complexity Tracking table omitted.

## Project Structure

### Documentation (this feature)

```text
specs/changes/004-trace-extraction/
├── plan.md              ← this file
├── research.md          ← Phase 0 decisions
├── data-model.md        ← Phase 1: Pydantic models + DB migration + UI testids
├── quickstart.md        ← Phase 1: local verification steps
├── contracts/
│   └── openapi.yaml     ← Phase 1: export-jobs schema delta + new test endpoint
└── tasks.md             ← Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/databridge/
├── export/
│   ├── models.py                 # + field_extraction: bool, field_extraction_path: str
│   │                              #   on ExportJob / ExportJobCreate / ExportJobResponse
│   │                              #   + model_validator: field_extraction=True requires
│   │                              #     non-empty field_extraction_path (422 on violation)
│   │                              # + FieldExtractionTestRequest / FieldExtractionTestResponse
│   ├── extraction.py             # NEW — extract_field_value(record, field_path) -> Any | None
│   │                              #   read-only counterpart to masking's dotted-path descent;
│   │                              #   shared traversal helper factored out of masking.py to
│   │                              #   satisfy DRY (Constitution §IV)
│   ├── db.py                     # insert/read mapping for the two new columns
│   └── worker.py                 # new per-record stage: sampling → field extraction →
│                                  #   masking → assets → sink (extraction failure ⇒
│                                  #   records_skipped++, continue)
│
├── export_metrics.py             # + EXPORT_FIELD_EXTRACTION_SUCCESS / _FAILED counters
│
├── routes/
│   └── connections.py             # + POST /connections/{id}/test-field-extraction
│                                  #   (mirrors test-asset-resolution: preview sample
│                                  #    records, report per-record resolution outcome)
│
├── db/migrations/versions/
│   └── 0008_field_extraction.py  # NEW — ALTER TABLE export_jobs ADD field_extraction,
│                                  #   field_extraction_path
│
├── templates/
│   └── browser.html               # + Field Extraction toggle + field-path input +
│                                  #   Test button, in the export config block
│                                  #   (sibling of the existing asset-resolution block)
│
└── static/
    └── browser.js                 # + field-extraction toggle wiring, test-call handler,
                                  #   results rendering, include fields in job creation payload

tests/
├── unit/
│   ├── test_extraction.py        # NEW — extract_field_value: nested paths, JSON-string
│   │                              #   containers, missing field, non-JSON string, empty value
│   ├── test_export_worker.py     # + stage-order test (extraction before masking),
│   │                              #   skip-and-count behavior
│   └── test_export_jobs_routes.py # + validator test: field_extraction=True + empty
│                                  #   field_extraction_path → 422
└── integration/
    └── test_datasinks_extraction.py  # NEW — POST test-field-extraction endpoint,
                                  #   end-to-end job run with extraction enabled

specs/current/
├── export-pipeline.md            # Worker Record Loop order updated (post-implementation
│                                  #   merge-back per Constitution §VIII)
└── field-extraction.md           # NEW — implemented-contract doc, mirrors
                                  #   asset-resolution.md's structure
```

**Structure Decision**: No new packages. `export/extraction.py` joins `export/masking.py`, `export/asset.py`, `export/sampling.py` as a fourth per-record transform module, each with a single reason to change (Constitution §IV — Interface Segregation: one module per transform, no fat interfaces). The worker's record loop gains one more conditional stage, following the exact pattern already used for sampling/masking/assets.

## Architecture Sequence (per Constitution §VII)

```
data-model.md  →  contracts/openapi.yaml  →  failing test stubs  →  implementation  →  refactor
```

1. Pydantic model additions + migration + UI testid map (`data-model.md`) — done (Phase 1)
2. OpenAPI delta (`contracts/openapi.yaml`) — done (Phase 1)
3. Failing test stubs (`test_extraction.py`, worker order test, validator test, endpoint test) — Phase 2 (`tasks.md`)
4. Implementation order: `export/extraction.py` → `export/models.py` (+ validator) → migration `0008` → `export/db.py` → `export_metrics.py` → `export/worker.py` → `routes/connections.py` (test endpoint) → `browser.html`/`browser.js` → `specs/current/` merge-back
5. Refactor under green tests

## Key Design Decisions (from research.md)

| # | Decision | Rationale |
|---|---|---|
| 1 | Extraction runs before masking, after sampling | Masking must protect the record that actually reaches the sink; if it ran before extraction it would be masking fields in a record that's about to be discarded |
| 2 | "Usable content" = native dict/list, or a string that `json.loads`s into a dict/list | Resolved with user in spec clarification (FR-007) — plain non-JSON strings are a skip, not a pass-through |
| 3 | Read-only traversal helper factored out of `masking._apply_at_path`, reused by both | Constitution §IV DRY — the JSON-string-transparent dotted-path descent is identical logic; duplicating it would violate the "logic duplicated across ≥2 modules MUST be extracted" rule |
| 4 | Single `field_extraction_path: str` column, not a list | Matches spec's single-key requirement; keeps this a sibling of `asset_resolution`'s shape, not `masking_rules`'s |
| 5 | Reject invalid config (`field_extraction=True` + empty path) via Pydantic `model_validator` at job creation | Spec FR-003 requires rejection at configuration time, not silent no-op; catches misconfiguration before a job burns through records skipping every one |
| 6 | New `POST /connections/{id}/test-field-extraction` preview endpoint | Mirrors `test-asset-resolution`; without it, a wrong field path is only discoverable after running a full job and seeing 100% skipped, which fails SC-001's "without needing a post-processing step" bar |
| 7 | Pipeline order change treated as a formal contract update | Constitution §VIII — order changes require updated spec + acceptance test; done as explicit implementation tasks, not left implicit in code |
| 8 | Feature named `field_extraction`, generic, not `trace_extraction` | Resolved in spec clarification 2026-07-13 — the mechanism extracts any nested JSON value; a trace payload is one motivating example, not the scope boundary |
