<!--
  Sync Impact Report
  ==================
  Version change: (unversioned template) → 1.0.0
  Added principles:
    - I.   Model-First Design
    - II.  API-First (OpenAPI Blueprint)
    - III. Test-Driven Development (NON-NEGOTIABLE)
    - IV.  Code Quality (SOLID + DRY)
    - V.   UI Testability
    - VI.  Performance & Scalability
    - VII. Architecture Sequence
  Added sections:
    - Quality Gates
    - Governance
  Templates updated:
    - ✅ .specify/templates/plan-template.md  — Constitution Check gates filled
    - ✅ .specify/templates/spec-template.md  — Models + API Contract sections added; data-testid notation
    - ✅ .specify/templates/tasks-template.md — Architecture Sequence note added
  Deferred: None
-->

# dataimporter Constitution

## Core Principles

### I. Model-First Design

Every feature MUST begin with a data model before any code:

- Data entities → Pydantic model in `specs/[feature]/data-model.md`
- Complex flows → PlantUML class/sequence/component diagram in the same file
- UI features → component tree or wireframe annotation before HTML/CSS

No implementation PR is valid without a prior model artifact. Models expose design flaws at zero cost.

### II. API-First (OpenAPI Blueprint)

All HTTP surfaces MUST be specified as an OpenAPI 3.1 blueprint in `specs/[feature]/contracts/openapi.yaml` before implementation.

- Request/response schemas MUST reference shared Pydantic models (see [§I](#i-model-first-design))
- Each endpoint MUST include ≥1 success example and ≥1 error example
- Breaking schema changes MUST be versioned (`/v2/…`); no silent mutations

Contract-first enables parallel frontend/backend work and auto-generates test fixtures.

### III. Test-Driven Development (NON-NEGOTIABLE)

TDD cycle is mandatory: **write failing tests → approval → implement → refactor**

- Unit: pytest, `tests/unit/`, one file per module
- Integration: MUST hit real adapters — no mocked backends; mocks of internal services are invalid
- Acceptance: Gherkin scenarios in `spec.md` → `pytest-bdd` stubs or Playwright scripts
- UI: Playwright selectors MUST use `data-testid`; class/position selectors are forbidden

Red-Green-Refactor is enforced. Failing test commit MUST precede implementation commit in git log.

### IV. Code Quality (SOLID + DRY)

- **S** — each module/class has one reason to change
- **O** — add datasource variants via new adapter; never modify existing adapters
- **L** — all adapters MUST be substitutable; all implement `BaseAdapter`
- **I** — search and export are separate adapter methods; no fat interfaces
- **D** — services depend on `BaseAdapter`, never on `ClickHouseAdapter` directly

DRY: shared logic lives in `src/dataimporter/adapters.py`. Logic duplicated across ≥2 adapters MUST be extracted.

Comments only for WHY (hidden constraint, invariant, workaround). Never explain WHAT — identifiers do that.

### V. UI Testability

Every interactive UI element MUST carry a stable `data-testid` attribute:

- Format: `data-testid="kebab-name"`, referenced in specs as `#kebab-name`
- IDs MUST survive re-renders and CSS refactors
- Spec notation example: search field `#search-input`, export trigger `#export-btn`, job status `#job-status-{id}`

Playwright test files MUST use `getByTestId(…)` or `[data-testid="…"]` — never CSS class or DOM index.

### VI. Performance & Scalability

The service MUST handle concurrent requests without degradation:

- Search endpoints: ≥50 concurrent requests at p95 ≤500 ms
- Worker `max_jobs=1` is intentional (OOM prevention); increase only after memory profiling + load test
- All adapter calls MUST be `async`; no blocking I/O in the event loop
- Every new adapter/route MUST export a Prometheus latency histogram + error counter
- Redis-backed queue is required for export; synchronous fallback is development-only

Load testing MUST be an acceptance criterion for any new adapter or export path.

### VII. Architecture Sequence

Work order is enforced for every feature:

```
Model / Diagram  →  OpenAPI Contract  →  Gherkin Scenarios + failing tests  →  Implementation  →  Refactor
```

1. Pydantic models or PlantUML diagram (`data-model.md`)
2. OpenAPI contract (`contracts/openapi.yaml`)
3. Gherkin acceptance scenarios + failing test stubs
4. Implementation (make tests pass)
5. Refactor under green tests

No feature is planned, estimated, or reviewed out of this order.

### VIII ## Spec-first change policy

All behavior changes MUST start from a spec change.

Every feature or bugfix MUST update one of:

- `/specs/current/*`
- `/specs/changes/<change-id>/*`

Implementation without a corresponding spec update is not allowed, except for emergency hotfixes. Emergency hotfixes MUST be followed by a spec update before the next planned release.

#### Current specs are source of truth

The `/specs/current` directory describes the current expected behavior of the service.

Feature specs under `/specs/changes` describe proposed modifications and MUST be merged back into `/specs/current` after implementation.

#### Pipeline order is contract

For import/export pipelines, the declared step order in `/specs/current/import-pipeline.md` and `/specs/current/export-pipeline.md` is part of the behavioral contract.

Any change to step order MUST be treated as a behavior change and requires:

- updated spec
- updated acceptance tests
- migration/compatibility notes if existing users are affected

### IX Requirement traceability

Each important behavior requirement SHOULD have a stable requirement ID.

Example:

- `REQ-IMPORT-001`
- `REQ-SAMPLING-001`
- `REQ-ASSET-001`

Acceptance tests SHOULD reference requirement IDs.

#### No silent drift

Code, tests, API contracts, and documentation MUST not contradict `/specs/current`.

Pull requests that change service behavior without updating specs SHOULD be rejected.

## Quality Gates

Every PR MUST satisfy:

| Gate | Requirement |
|---|---|
| Model exists | `specs/[feature]/data-model.md` with Pydantic or PlantUML present |
| Contract exists | `specs/[feature]/contracts/openapi.yaml` present for any HTTP change |
| Tests first | Failing test commit precedes implementation commit in git log |
| Test IDs | All new UI elements carry `data-testid` matching spec notation |
| Async I/O | No synchronous adapter calls in the event loop |
| Metrics | New adapter/route exports Prometheus counter + histogram |
| Performance | p95 ≤500 ms verified by load test or written exception + rationale |

## Governance

This constitution supersedes all other practices and guidance files.

**Amendments**:
1. Bump version (rules below) and update `Last Amended`
2. Propagate changes to all `.specify/templates/`
3. Open PR with label `constitution-amendment`; requires ≥1 reviewer approval

**Versioning**:
- MAJOR — principle removal or redefinition (backward-incompatible governance change)
- MINOR — new principle or section added
- PATCH — clarification, wording, or typo fix

All PRs MUST pass the [Quality Gates](#quality-gates). Runtime guidance lives in `CLAUDE.md`.

**Version**: 1.0.1 | **Ratified**: 2026-06-01 | **Last Amended**: 2026-06-01
