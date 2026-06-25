# Implementation Plan: Browser UI Redesign

**Branch**: `002-browser-ui-redesign` | **Date**: 2026-06-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/changes/003-browser-ui-redesign/spec.md`

## Summary

Redesign the browser SPA from a two-column connection-card layout to a full-page tabbed interface (Data Import / Jobs) that matches the reference design in `specs/changes/ui-reference/`. The new layout provides: horizontal connection tabs with health status, a Refine Dataset card with schema discovery + predicate filter + structured rule builder, a Data Preview table with colored status badges + Load More, a Data Masking card, a Sampling Strategy card, an Export & Destination section with a prominent Export button, and a Webhook Configuration card. Backend changes: `FilterSnapshot` gains `time_field` + `limit`; `ExportJobCreate` gains `masking_rules`, `sampling_config`, `webhook_url`, `webhook_enabled`; the preview response gains `total_count`; a new `/pii-fields` endpoint is added; the ARQ worker applies masking and sampling and fires the webhook on completion; Alembic migration 0003 adds four new columns.

## Technical Context

**Language/Version**: Python 3.13 (backend), vanilla ES2020 JS (frontend)

**Primary Dependencies**: FastAPI 0.135+, uvicorn, asyncpg, Tailwind CSS (CDN), Material Symbols Outlined (CDN), Public Sans (Google Fonts CDN), arq, redis[hiredis], structlog, prometheus-client

**Storage**: PostgreSQL — Alembic migration 0003 adds `masking_rules` (JSONB), `sampling_config` (JSONB nullable), `webhook_url` (TEXT nullable), `webhook_enabled` (BOOLEAN) to `export_jobs` table

**Testing**: pytest, pytest-asyncio (strict), respx (HTTP mocking for outbound webhook calls in unit tests), pytest-bdd (Gherkin stubs), playwright + pytest-playwright (E2E with `data-testid` selectors)

**Target Platform**: Linux server / docker-compose (same as ai-suite); desktop browser ≥ 1024px viewport

**Project Type**: Web service — FastAPI REST API + vanilla-JS SPA + ARQ background worker

**Performance Goals**: API endpoints p95 ≤ 500 ms; preview `total_count` COUNT query completes within the same response budget; webhook delivery is fire-and-forget (no blocking the worker finalize path)

**Constraints**: No framework, no build step, no bundler; Tailwind Play CDN only; all JS in `browser.js`; all styles in `browser.css`; mobile layout out of scope; design tokens added via CSS custom properties in `browser.css`

**Scale/Scope**: Single-page app; all state in JS module scope (no localStorage); polling interval for jobs view: 3 s (unchanged)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| Model exists | ✅ | `data-model.md` created — updated Pydantic models, DB migration, UI component tree, data-testid map |
| Contract exists | ✅ | `contracts/openapi-delta.yaml` — new `/pii-fields` endpoint + updated `/preview` and `/export-jobs` schemas |
| Tests first | ✅ | Failing stubs for masking, sampling, webhook, and Playwright E2E committed before implementation |
| Test IDs | ✅ | All 50+ interactive UI elements carry `data-testid` (see data-model.md §7) |
| Async I/O | ✅ | All new adapter methods async; webhook delivery via `httpx.AsyncClient`; COUNT query via asyncpg |
| Metrics | ✅ | `masking_rules_applied_total` counter; `sampling_records_dropped_total` counter; `webhook_delivery_total` counter (labels: status=success/failure) |
| Performance | ✅ | COUNT query runs in parallel with data fetch; webhook is non-blocking (fire-and-forget via background task) |

No violations — Complexity Tracking table omitted.

## Project Structure

### Documentation (this feature)

```text
specs/changes/003-browser-ui-redesign/
├── plan.md              ← this file
├── research.md          ← Phase 0: 10 decisions
├── data-model.md        ← Phase 1: updated models + DB schema + UI component tree + testid map
├── quickstart.md        ← Phase 1: local dev setup and feature verification
├── contracts/
│   └── openapi-delta.yaml  ← Phase 1: API changes (new endpoint + updated schemas)
└── tasks.md             ← Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/databridge/
├── export/
│   ├── models.py           # + MaskingAction enum, MaskingRule, SamplingMethod enum,
│   │                       #   SamplingConfig; extend FilterSnapshot with time_field + limit;
│   │                       #   extend ExportJobCreate/ExportJob/ExportJobResponse with
│   │                       #   masking_rules, sampling_config, webhook_url, webhook_enabled
│   ├── masking.py          # NEW — apply_masking(record, rules) → dict
│   │                       #   mask/hash/drop/redact actions; PII name-heuristic detector
│   ├── sampling.py         # NEW — SamplingBuffer for random/systematic/stratified strategies
│   ├── webhook.py          # NEW — deliver_webhook(url, payload) via httpx.AsyncClient
│   │                       #   fire-and-forget; logs on failure; no retry in v1
│   ├── db.py               # extend insert_export_job + get_export_job + list_export_jobs
│   │                       #   to read/write the four new columns
│   └── worker.py           # + call apply_masking() per record when masking_rules present
│                           # + call SamplingBuffer when sampling_config present
│                           # + call deliver_webhook() after finalise() when webhook_enabled
│
├── routes/
│   ├── connections.py      # extend /preview response with total_count + time_field support;
│   │                       # add GET /api/v1/connections/{id}/pii-fields
│   └── export_jobs.py      # pass new fields through create/retry (no logic change)
│
├── export_metrics.py       # + masking_rules_applied_total Counter (labels: org_id)
│                           # + sampling_records_dropped_total Counter (labels: org_id)
│                           # + webhook_delivery_total Counter (labels: org_id, status)
│
├── db/
│   └── migrations/versions/
│       └── 0003_export_job_enhancements.py  # ADD COLUMN masking_rules JSONB NOT NULL DEFAULT '[]';
│                                            # ADD COLUMN sampling_config JSONB;
│                                            # ADD COLUMN webhook_url TEXT;
│                                            # ADD COLUMN webhook_enabled BOOLEAN NOT NULL DEFAULT FALSE
│
├── templates/
│   └── browser.html        # FULL REWRITE — new tabbed layout:
│                           #   TopNav with Data Import / Jobs nav tabs
│                           #   ConnectionTabBar (horizontal pill tabs + sync status)
│                           #   RefineDatasetCard (schema discovery + filter row)
│                           #   DataPreviewSection (table + visibility + load more)
│                           #   DataMaskingCard (toggle + rules table + PII detection)
│                           #   SamplingStrategyCard (toggle + method + target + ratio)
│                           #   ExportDestinationSection (sink select + Export button)
│                           #   WebhookConfigCard (toggle + URL + Test button)
│                           #   JobsView (job rows with status/progress/retry/download)
│                           #   ConnModal (unchanged from existing)
│
└── static/
    ├── browser.js          # FULL REWRITE — new SPA logic:
    │                       #   state: _connections, _activeId, _schema, _filterState,
    │                       #          _maskingRules, _samplingConfig, _webhookConfig,
    │                       #          _previewRows, _previewLimit, _totalCount
    │                       #   renderConnectionTabBar(), renderSchemaSection(),
    │                       #   renderPreviewTable() with status badge rendering,
    │                       #   renderDataMaskingCard(), renderSamplingCard(),
    │                       #   renderJobsView()
    │                       #   auto-schema-discover on tab select
    │                       #   CLEAR ALL logic (watches all filter state)
    │                       #   structured filter builder (AND/OR toggle when ≥2 rules)
    │                       #   Load More (doubles limit, re-fetches)
    │                       #   PII auto-detect (calls /pii-fields, populates masking table)
    │                       #   webhook test (calls /api/v1/export-jobs/test-webhook)
    └── browser.css         # + design token CSS variables (--color-primary etc.)
                            # + tab bar styles (.conn-tab, .conn-tab--active)
                            # + schema discovery section (.schema-section)
                            # + field chip + type badge (.field-chip, .type-badge-pill)
                            # + status badge variants (.status-badge--processed,
                            #   --error, --pending)
                            # + data masking card (.masking-card)
                            # + sampling card (.sampling-card)
                            # + export section (.export-section)
                            # + webhook card (.webhook-card)
                            # + pulsing dot animation (.dot-pulse)

tests/
├── unit/
│   ├── test_masking.py     # apply_masking: mask/hash/drop/redact; PII heuristic
│   └── test_sampling.py    # random/systematic/stratified buffer; edge cases (empty, all)
├── integration/
│   └── test_export_jobs.py # extend existing: create job with masking_rules + sampling_config;
│                           # verify DB columns stored/retrieved; verify worker applies them
└── e2e/
    └── test_browser_redesign.py  # Playwright: tab navigation, schema discovery flow,
                                  # predicate filter, masking UI, sampling UI,
                                  # export → jobs view → status badges
```

**Structure Decision**: Same FastAPI single-project layout. New behaviour is split into three narrow modules (`masking.py`, `sampling.py`, `webhook.py`) under `export/` following Constitution §IV (S — single reason to change). The SPA rewrite stays in the existing `browser.html` / `browser.js` / `browser.css` trio — no new static files.

## Architecture Sequence (per Constitution §VII)

```
data-model.md  →  contracts/openapi-delta.yaml  →  failing test stubs  →  implementation  →  refactor
```

1. Pydantic model additions + DB migration + UI component tree + testid map (`data-model.md`) — **done**
2. OpenAPI delta contract (`contracts/openapi-delta.yaml`) — **done**
3. Gherkin acceptance stubs + pytest-bdd skeletons (failing) — Phase 2 (tasks.md)
4. Implementation order: migration → new export modules → route changes → SPA rewrite
5. Refactor under green tests

## Key Design Decisions (from research.md)

| # | Decision | Rationale |
|---|---|---|
| 1 | Vanilla JS, no framework | No build toolchain; existing pattern; Playwright testid selectors sufficient |
| 2 | Tailwind CDN + design token CSS vars | No new CDN dependency; 3 custom properties cover all reference colors |
| 3 | CSS show/hide for tabs | No client-side router; single server route; established pattern |
| 4 | Connection tab bar replaces card list | Reference screenshot; selectConnection() refactored, not replaced |
| 5 | Masking: client list → worker applies | Masking at full export scale, not preview scale |
| 6 | Sampling: config in export job body → worker | Full-pass strategies need server-side access to all records |
| 7 | Webhook: per-job field, worker fires on completion | Reference UI places it inline with per-export config |
| 8 | time_field in FilterSnapshot | Reference badge is user-selectable; adapters need to know which column |
| 9 | total_count in preview response | Reference shows "TOTAL: 4.2M ROWS"; parallel COUNT + fetch avoids race |
| 10 | PII detection: server-side name heuristic | Reuses schema machinery; extensible per adapter |
