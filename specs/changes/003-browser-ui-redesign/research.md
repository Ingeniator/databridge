# Research: Browser UI Redesign

**Branch**: `002-browser-ui-redesign` | **Date**: 2026-06-03

Ten decisions resolved during Phase 0 research against the existing codebase and ui-reference screenshots.

---

## Decision 1 — Keep vanilla JS, no frontend framework

**Decision**: Retain the existing vanilla-JS approach; no React/Vue/Svelte.

**Rationale**: The existing codebase is a single 788-line `browser.js` file with no build toolchain, npm, or bundler. Introducing a framework would require a new build pipeline, violate the single-project constraint, and outscope a UI redesign. The reference design is achievable in plain JS + Tailwind.

**Alternatives considered**: Alpine.js (no-build reactive micro-framework) — rejected because it adds a CDN dependency and obscures the data-testid patterns Playwright relies on. HTMX — rejected because it changes the server-side rendering model.

---

## Decision 2 — Keep Tailwind CDN + Material Symbols Outlined

**Decision**: Continue using the Tailwind Play CDN and the Material Symbols Outlined icon font, supplemented by design-token CSS variables where Tailwind's default palette doesn't cover the reference colors (primary `#094cb2`, `primary-fixed` `#d9e2ff`, `tertiary-container` `#bfab49`).

**Rationale**: No build step exists; these CDN links are already in `browser.html`. Adding three CSS custom properties in `browser.css` is zero-overhead.

**Alternatives considered**: None — changing icon fonts or CSS framework is out of scope.

---

## Decision 3 — Tab navigation: CSS show/hide, no client-side router

**Decision**: "Data Import" and "Jobs" are toggled via `display:none / block` on two root sections. The URL does not change.

**Rationale**: The service has a single route (`GET /`) that returns `browser.html`. There is no client-side router and no history API integration. Show/hide is the established pattern (see existing `jobs-panel` overlay).

**Alternatives considered**: `history.pushState` for deep-linking — rejected because it requires server-side catch-all routing changes and is out of scope.

---

## Decision 4 — Connection tab bar replaces connection card list

**Decision**: Remove the two-column card layout. Connections become horizontal pill tabs above the Refine Dataset card. Schema discovery auto-triggers on tab selection.

**Rationale**: The reference screenshots show this layout explicitly. The existing `selectConnection()` function already handles "active connection" state; it is refactored, not replaced.

**Alternatives considered**: Keep cards and add a horizontal summary bar — rejected as inconsistent with the reference.

---

## Decision 5 — Masking rules: client-side list, applied by worker

**Decision**: The Data Masking card collects a list of `{field_path, action}` rules in browser state. On Export these are serialised into `ExportJobCreate.masking_rules`. The ARQ worker applies them record-by-record before writing to the sink.

**Rationale**: Masking must run on full export data (not preview), so client-side preview-time masking is insufficient. `specs/current/masking.md` is marked "not ready yet" — this feature defines it.

**Alternatives considered**: Server-side masking endpoint (send a sample, get back masked rows) — rejected as duplicating work the worker already does.

---

## Decision 6 — Sampling config: sent in export job body, applied by worker

**Decision**: `SamplingConfig` (`method`, `target_column`, `ratio_or_size`) is an optional field on `ExportJobCreate`. When set, the worker samples records using the configured strategy before writing.

**Rationale**: Sampling on millions of rows must happen in the worker, not the browser. Stratified sampling requires a full pass over the data; simple random sampling needs the full record set to derive the count.

**Alternatives considered**: Preview-time sampling (show sampled rows) — out of scope; the sampled preview can be faked by using a low `limit` value.

---

## Decision 7 — Webhook: per-job field, sent by worker on completion

**Decision**: Add `webhook_url: str | None` and `webhook_enabled: bool` to `ExportJobCreate`. When `webhook_enabled=True` and `webhook_url` is set, the worker sends a `POST` to that URL after finalising the job (success or failure).

**Rationale**: The reference screenshot shows a "Webhook Configuration" card with a "RUN ON COMPLETION" toggle inline with the export form, making it per-job rather than org-level config.

**Alternatives considered**: Org-level webhook in server config — rejected because the reference UI places it inside the per-export workflow, implying per-job configuration.

---

## Decision 8 — Time field selector stored in FilterSnapshot

**Decision**: Add `time_field: str | None` to `FilterSnapshot`. When the user changes the timestamp field badge, the selected field name is recorded here. The backend's existing `start`/`end` filter is applied against this field when provided.

**Rationale**: The reference screenshot shows a "FIELD: TIMESTAMP ↔" badge that is user-selectable from schema-discovered fields. The filtering logic in adapters already uses `start`/`end`; `time_field` tells each adapter which column to compare against.

**Alternatives considered**: Hard-code a default timestamp column per adapter — rejected because the schema discovery section explicitly surfaces this choice to the user.

---

## Decision 9 — Add `total_count` to preview response

**Decision**: The `POST /api/v1/connections/{id}/preview` response gains a `total_count: int` field representing the unbounded row count for the current filter (before the `limit` is applied). Each adapter runs a `COUNT(*)` query in parallel with the data fetch.

**Rationale**: The reference screenshot shows `TOTAL: 4.2M ROWS` in the Data Preview header. The export job creation flow also benefits from knowing total record volume before committing to an export.

**Alternatives considered**: A dedicated `/count` endpoint — unnecessary roundtrip; combining count + fetch in one response is simpler and eliminates a race condition where count and data diverge between calls.

---

## Decision 10 — PII auto-detection: schema-name heuristic, server-side

**Decision**: A new `GET /api/v1/connections/{id}/pii-fields` endpoint returns candidate PII field names using a name-pattern heuristic (e.g. fields containing `email`, `phone`, `ssn`, `password`, `ip_address`, `user_id`). The UI populates the masking table with these candidates pre-checked when PII auto-detection is toggled.

**Rationale**: The reference screenshot shows a "PII AUTO-DETECTION ENABLED" toggle in the Data Masking card. A server-side heuristic is more reliable than client-side parsing of schema field names, and reuses the schema detection machinery.

**Alternatives considered**: Client-side heuristic on schema field names — simpler but doesn't benefit from server knowledge of field values or adapter-specific PII conventions.
