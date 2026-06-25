# Feature Specification: Browser UI Redesign

**Feature Branch**: `002-browser-ui-redesign`

**Created**: 2026-06-03

**Status**: Draft

**Input**: User description: "redesign UI interface based on ui-reference screenshots"

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Browse & Filter Data from a Datasource (Priority: P1)

A user opens the browser page, selects a datasource connection from the tab bar, and uses the Refine Dataset card to narrow down records. They set a time range, write a predicate filter, and see matching rows in the Data Preview table below — all without leaving the page.

**Why this priority**: This is the core read-side workflow. Every downstream action (export, masking, sampling) depends on the user first being able to locate and preview the right records.

**Independent Test**: Can be fully tested by loading the browser page, selecting a connection, applying a time + predicate filter, and confirming matching rows appear in the preview table. Delivers standalone value as a data exploration tool.

**Acceptance Scenarios**:

1. **Given** the browser page is open, **When** the user clicks a connection tab, **Then** the Refine Dataset card shows the schema discovery section and the Data Preview table populates with rows from that connection.
2. **Given** a connection is active and schema has loaded, **When** the user selects "Last 24 hours" and types `status == 'error'` in the predicate filter, **Then** only matching rows appear in the Data Preview table and the total row count updates.
3. **Given** at least one filter is active, **When** the user clicks "CLEAR ALL", **Then** all filters reset to defaults and the full unfiltered preview reloads.
4. **Given** the Data Preview table is loaded, **When** the user clicks "Load More Rows", **Then** additional rows are appended to the table without a full page reload.

---

### User Story 2 — Configure and Launch a Data Export (Priority: P2)

After previewing the right dataset, the user scrolls to the Export & Destination section, optionally enables data masking and sampling, selects a destination sink, and clicks Export. A background job starts and the user can navigate to the Jobs tab to track progress.

**Why this priority**: This is the primary value-delivery action of the product — getting data out to a downstream system.

**Independent Test**: Can be fully tested by selecting a datasink, clicking Export, and confirming a job entry appears in the Jobs tab with a running status.

**Acceptance Scenarios**:

1. **Given** a datasource is selected and a destination sink is chosen, **When** the user clicks Export, **Then** an export job is created and the Jobs tab badge shows a new active job.
2. **Given** the Data Masking section is enabled, **When** the user adds a field masking rule and clicks Export, **Then** the exported records have the specified field masked.
3. **Given** the Sampling Strategy section is enabled, **When** the user sets a ratio of 0.10 and clicks Export, **Then** only ~10% of matching records are written to the destination.
4. **Given** an export completes, **When** the user had configured a webhook URL, **Then** the webhook receives a completion notification.

---

### User Story 3 — Monitor Export Jobs (Priority: P3)

The user navigates to the Jobs tab to see the history of export jobs — their status, record counts, and completion time. They can retry a failed job.

**Why this priority**: Observability is important but does not block the primary export workflow. Users can still export without viewing job history.

**Independent Test**: Can be fully tested by triggering an export and then viewing its status entry in the Jobs tab, including progress and final result.

**Acceptance Scenarios**:

1. **Given** one or more export jobs exist, **When** the user clicks the Jobs tab, **Then** a list of jobs is shown with status badges (running, completed, failed) and record counts.
2. **Given** a job has failed, **When** the user clicks Retry, **Then** a new job is created with the same parameters and status resets to running.

---

### Edge Cases

- What happens when schema detection fails (network error or unsupported format)?
- How does the predicate filter behave when the expression is syntactically invalid?
- What happens when the selected sink is unreachable at export time?
- What happens when the user tries to export while a concurrent job is already running for the same org?
- How does the time range filter behave when the selected timestamp field contains nulls?

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The page MUST provide top-level navigation tabs: "Data Import" and "Jobs".
- **FR-002**: The connection tab bar MUST display all configured datasource connections; the active tab MUST be visually distinguished (bold label + colored underline).
- **FR-003**: A "+ Add Connection" action MUST appear at the end of the connection tab list.
- **FR-004**: The active connection MUST display a last-synced relative timestamp and a health status indicator with at least three states: Healthy, Syncing, and Error.
- **FR-005**: The Refine Dataset card MUST include a Schema Discovery section that auto-detects available fields, displays up to 3 representative field chips (with data type labels), and supports collapse/expand.
- **FR-006**: The Schema Discovery section MUST show the total count of detected fields and allow the user to re-trigger detection.
- **FR-007**: The Refine Dataset card MUST include a time range filter tied to a user-selectable timestamp field; when no timestamp field is designated, the time picker MUST be disabled.
- **FR-008**: The Refine Dataset card MUST include a free-form predicate filter input; the filter MUST also support a structured rule builder (field + operator + value rows) accessible via an advanced toggle.
- **FR-009**: A "CLEAR ALL" control MUST be visible only when at least one filter, time range selection, or search expression is active.
- **FR-010**: The Data Preview section MUST render data in a table with schema-driven columns; users MUST be able to toggle column visibility.
- **FR-011**: The Data Preview table MUST show the current result limit and total matching row count in the header.
- **FR-012**: Status/level/state column values MUST render as colored pill badges (green/amber/red/blue) rather than plain text.
- **FR-013**: When the displayed row count equals the current limit, a "Load More Rows" action MUST appear to fetch additional records.
- **FR-014**: A Data Masking section MUST allow users to define per-field masking rules (at minimum: Mask, Hash actions) and MUST support automatic PII field detection.
- **FR-015**: A Sampling Strategy section MUST allow users to choose a sampling method, configure the target column, and set a sample ratio or size; the section MUST be toggleable (disabled by default).
- **FR-016**: An Export & Destination section MUST allow users to enable optional asset URL resolution and select a configured destination sink from a dropdown.
- **FR-017**: An Export button MUST initiate an export job using the current filter state, masking rules, sampling config, and selected sink.
- **FR-018**: A Webhook Configuration section MUST allow users to enter a URL for post-export notification, toggle it on/off, and test the webhook with a dedicated action.
- **FR-019**: The Jobs tab MUST list export jobs with status badges, record counts, and timestamps; failed jobs MUST offer a Retry action.

### Key Entities

- **Connection**: A configured datasource (name, type badge, sync timestamp, health state).
- **Schema Field**: A detected data field with a name (dot-path) and an inferred data type.
- **Filter State**: The combination of active time range, timestamp field selection, predicate expression, and structured filter rules.
- **Export Job**: A background task record tracking sink, filter snapshot, progress, status, and timestamps.
- **Masking Rule**: A per-field instruction (field path + action) applied during export.
- **Sampling Config**: A method + target column + ratio/size applied to limit exported record volume.
- **Datasink**: A configured export destination (name, type, connection parameters).

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can select a connection, apply a filter, and see matching rows in the Data Preview table within 5 seconds of interaction.
- **SC-002**: Schema discovery completes and field chips are visible without any manual field configuration.
- **SC-003**: Users can configure a full export (connection → filter → masking → sampling → sink) and click Export in under 3 minutes on first use.
- **SC-004**: All interactive controls (tabs, filters, toggles, table actions) are reachable and operable via keyboard alone.
- **SC-005**: The Jobs tab accurately reflects the current status of all export jobs; status updates appear within 5 seconds of a state change.
- **SC-006**: 95% of users can locate the Export button without assistance on their first session.

---

## Assumptions

- The existing datasource connection configuration remains unchanged; connections are read from server config, not managed in this UI.
- The page is a single-page interface rendered server-side with client-side interactivity (no full-page reloads for filter changes).
- Mobile layout is out of scope for this redesign; the target viewport is desktop (≥1024px wide).
- The color and typography design tokens (primary blue, surface containers, monospace font for field paths) from the existing design system are reused without modification.
- The Jobs tab displays jobs scoped to the current user's organization; cross-org visibility is an admin-only concern handled elsewhere.
- Webhook testing sends a real HTTP request to the configured URL; no sandbox/mock mode is required for v1.
