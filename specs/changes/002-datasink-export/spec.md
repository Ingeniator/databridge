# Feature Specification: Datasink Export Pipeline

**Feature Branch**: `001-datasink-export`

**Created**: 2026-06-02

**Status**: Draft

**Input**: User description: "export dataset from selected datasource to configured datasink with UI datasink selector, asset resolution toggle, background worker job processing, progress tracking, and multiple sink types (dataset-mock, annotator-mock, local ZIP archive, local JSONL)
I want to add feature to export dataset from selected datasource to configured  datasink'/var/folders/bn/snrghsfn7y7gbtbrs_g0wgpr0000gn/T/TemporaryItems/NSIRD_screencaptureui_hsG8U8/Screenshot 2026-06-02 at 10.03.51.png'           we should add in ui such block where user will select datasink (and select dataset name to export data in) and toggle mode - should we make asset      resolution (upload linked data and seve it in separate dataset, we need configureit too) after user press Export - we collect selected filter and      datasource and make export job that should process with other worker-container. we need count progress and show it to user. and be able to give a list of exporting data. export params will be extended later (we will add masking, sampling, webhook on other phase). datasink should have basic abstract  class to extend. as datasink we should use dataset-mock service, annotator-mock service those we had in dataimporter and ai-suite projects, also we need to construct datasink to save file locally as zip archive with files, and another one - to save one jsonl file with one lone per "file" if data is json serializable.  "

## Clarifications

### Session 2026-06-02 (round 1)

- Q: Is the export jobs list scoped per user or shared across all users of the service? → A: Role-based via X-GROUP-ID header (`<org_id/user_id>`). Three roles: `super_admin` sees all jobs; `org_admin` sees all jobs belonging to their org; `user` sees only their own jobs.
- Q: What is the stale job timeout (when worker dies mid-export)? → A: 15 minutes, configurable via service settings.
- Q: Is there a cap on concurrent export jobs? → A: Per-org cap, default 5 concurrent jobs, configurable via service settings.
- Q: How are individual files named inside the ZIP archive? → A: User-defined filename template per datasink connection, with field references drawn from the discovered datasource schema (e.g., `{id}_{timestamp}`); falls back to content hash when the template cannot be resolved for a record.
- Q: What write protocol do dataset-mock and annotator-mock sinks use? → A: A unified contract modelled on dataset-mock's API: list-datasets endpoint and post-file endpoint. Sinks whose native API differs (e.g., annotator-mock) expose an adapter layer that maps to this contract; no new protocol is introduced.

### Session 2026-06-02 (round 3)

- Q: How does the system identify which record fields contain asset URLs to resolve? → A: User-configured field list per export job; the UI pre-populates candidate fields by auto-detecting from the datasource schema using name conventions (e.g., fields named `url`, `file_url`, `image_url`, `asset_url`) and URL pattern presence in sample data. User confirms or adjusts the selection before initiating the export.
- Q: How are export progress updates delivered to the UI? → A: Client-side polling at a default 3-second interval, configurable via service settings.
- Q: Who can see and use datasink connections, and how are they managed? → A: Datasinks are defined in the service YAML config (not user-created) and are visible to all users globally. Datasink services enforce their own access control using the user's JWT token, which databridge will pass through in a future phase.
- Q: How long are completed/failed export jobs retained? → A: Configurable TTL, default 7 days; completed and failed jobs are automatically purged after the TTL expires.
- Q: What observability signals should the export pipeline expose? → A: Full pipeline metrics — route-level request counters and latency histograms (existing pattern), plus: jobs created/completed/failed counters, active jobs gauge, records-per-second throughput gauge, per-sink-type breakdowns, asset resolution success/fail counters, and per-org job counters.
- Q: What happens when an individual asset fails to fetch during export? → A: The entire record is skipped (not written to destination) because the record would contain a broken media reference; skipped records are counted on the job. Additionally, users must be able to configure an asset URL prefix per export job (prepended to relative asset paths/IDs before fetch); real-world example: trace data stores only resource IDs, not full URLs.
- Q: How is a new destination dataset created before the first file is posted? → A: Explicit creation — the protocol includes a `create-dataset` operation called before the first post. The assets dataset name is auto-derived as `{destination_dataset_name}_assets` (not user-entered); the UI shows this derived name as a read-only label in the asset resolution block.
- Q: Is a progress heartbeat the same as a progress update, or a separate signal? → A: Progress updates double as heartbeats; the worker also emits a keep-alive update (unchanged counters) at a configurable interval (default: 2 minutes) when records are processed slowly, preventing false-positive stale detection.
- Q: Where in the UI does the Export configuration appear? → A: Inline on the datasource page, below the Data Preview section in the same scrollable view. The Export & Destination block (Asset Resolution toggle + sink selector + Export button) is always visible when viewing a datasource. Export jobs list lives under a separate "Jobs" tab.
- Q: Does the export panel show a dedicated data preview, or does it reuse the existing Data Preview? → A: The existing Data Preview (with active filter applied and TOTAL row count shown) serves as the export preview; no separate preview component is needed. The TOTAL count in the preview table represents the number of records to be exported.

### Session 2026-06-02 (round 4)

- Q: Can exports be initiated from both user connections and administrator-defined system sources (YAML-configured)? → A: Both. The export job identifies its source via a type discriminator (`connection` or `system`) plus the appropriate identifier (DB id for user connections, config name for system sources).
- Q: When is `records total` determined for the export job? → A: The worker determines the total count before processing the first batch; the total is unknown (null) from job creation until the worker begins. The UI shows processed count without a percentage until total is set.
- Q: Can users retry a failed export job? → A: Yes — a Retry action on a failed job creates a new export job pre-filled with the failed job's settings (datasink, dataset name, filters, asset resolution config); no in-place resume; the original failed job remains unchanged in the list.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Export Data to Datasink (Priority: P1)

A user is browsing data in the datasource viewer, has applied filters, and wants to export the visible dataset to an external service or local file. They select a pre-configured datasink, choose or type a destination dataset name within that sink, and press Export. The system creates an export job, a background worker picks it up, and the user sees progress feedback without leaving the page.

**Why this priority**: This is the core end-to-end value of the feature — moving data from a source to a destination is the primary user goal. All other stories build on this.

**Independent Test**: Can be tested by selecting a YAML-configured datasink, opening a datasource, clicking Export, filling the form, confirming, and verifying the export job appears with progress updates. Delivers complete value even without asset resolution.

**Acceptance Scenarios**:

1. **Given** a datasource connection is selected and data is visible with optional filters applied, **When** the user scrolls to the Export & Destination block, selects a datasink, enters a dataset name, and presses Export, **Then** an export job is created and appears in the Jobs tab with status "pending" or "running"
2. **Given** an export job is created, **When** the background worker processes records, **Then** the progress counter updates in the UI showing processed vs. total record count
3. **Given** an export job completes, **When** the user views the jobs list, **Then** the job shows status "completed" with final record count and timestamp
4. **Given** an export job fails, **When** the user views the jobs list, **Then** the job shows status "failed" with an error message

---

### User Story 2 - Monitor Export Jobs (Priority: P2)

A user wants to see all ongoing and past export operations — their source, destination, status, progress, and timestamps — in a dedicated list view accessible from the main UI.

**Why this priority**: Once exports are running in the background, users need visibility to confirm success, diagnose failures, and track multiple concurrent exports.

**Independent Test**: Can be tested independently by triggering an export and verifying the jobs list panel shows correct status, source, sink name, record counts, and timestamps. Delivers monitoring value on its own.

**Acceptance Scenarios**:

1. **Given** one or more export jobs have been created, **When** the user opens the export jobs list, **Then** each job displays: datasource name, datasink name, destination dataset name, status, records processed / total, start time
2. **Given** an export job is in progress, **When** the user views the list, **Then** progress updates automatically without requiring a page reload
3. **Given** multiple export jobs exist in various states, **When** the user views the list, **Then** jobs are ordered by start time descending (newest first)
7. **Given** a job in "failed" status, **When** the user triggers Retry, **Then** a new export job is created with the same settings and appears at the top of the list; the original failed job remains visible
4. **Given** a `user` role token, **When** the jobs list is requested, **Then** only jobs owned by that user are returned
5. **Given** an `org_admin` role token with org_id `X`, **When** the jobs list is requested, **Then** all jobs with org_id `X` are returned regardless of individual owner
6. **Given** a `super_admin` role token, **When** the jobs list is requested, **Then** all jobs across all orgs and users are returned

---

### User Story 3 - Asset Resolution on Export (Priority: P3)

A user is exporting data that contains references to external files or linked assets (e.g., image URLs, file references). They want those assets to be fetched and stored in a separate dataset during the export so that the destination contains self-contained data rather than dangling links.

**Why this priority**: Asset resolution enriches export quality but is optional — the core export works without it. It requires configuring an additional target datasink for assets.

**Independent Test**: Can be tested by exporting data with known linked assets, enabling asset resolution, specifying a target dataset for assets, and verifying that assets appear in the target dataset and the main export records contain resolved references.

**Acceptance Scenarios**:

1. **Given** the export panel is open, **When** the user toggles "Resolve Assets" on, **Then** additional configuration fields appear: asset datasink selector, a read-only label showing the auto-derived asset dataset name (`{destination_dataset_name}_assets`), and a pre-populated candidate field list for asset URL resolution
2. **Given** asset resolution is toggled on, **When** the candidate field list is displayed, **Then** it contains fields auto-detected from the datasource schema by name convention (e.g., `url`, `file_url`, `image_url`, `asset_url`) and by URL pattern presence in sample data, with all candidates pre-selected for user review
2. **Given** asset resolution is enabled and configured, **When** the export job runs, **Then** linked assets are fetched and stored in the configured asset dataset, and the exported records reference the stored asset locations
3. **Given** asset resolution is enabled, configured with a confirmed field list, and an asset URL prefix is provided, **When** the export job runs, **Then** the prefix is prepended to each field value before the fetch attempt
4. **Given** asset resolution is enabled and an asset fetch for a specific record fails (404, timeout, or unresolvable URL), **When** the worker processes that record, **Then** the entire record is skipped (not written to the destination), the skip counter increments, and processing continues with the next record
5. **Given** asset resolution is enabled but the asset datasink is unreachable, **When** the export job runs, **Then** the job fails with a clear error indicating asset upload failure, and no partial records are written to the main destination

---

### Edge Cases

- What happens when the datasink is unreachable when the export job starts? The job should fail immediately with a connectivity error rather than waiting indefinitely.
- What happens if the datasource returns zero records matching the current filter? The job should complete successfully with 0 records exported and display that count.
- What happens if a record is not JSON-serializable for the JSONL sink? That record is skipped and counted in a "skipped" counter; the job continues and reports skipped count at completion.
- What happens if the ZIP sink runs out of disk space mid-export? The job fails with a storage error and cleans up the partial archive.
- What happens when the ZIP sink filename template references a schema field that is absent in a specific record? That record's filename falls back to its content hash; the job continues and the fallback count is reported at completion.
- What happens when two concurrent export jobs write to the same datasink and dataset name? Each job writes independently; the system does not prevent conflicts — the datasink is responsible for collision handling.
- What happens when a user tries to start an export but their org has already reached the concurrent job limit (default: 5)? The request is rejected immediately with an error indicating the limit and how many jobs are currently running for the org.
- What happens if the worker container goes down mid-export? The job remains in "running" state; a background sweep marks it "failed" after 15 minutes without any progress update or keep-alive signal (default timeout, configurable via service settings). Workers processing slow batches emit a keep-alive (default every 2 minutes, configurable) to avoid false-positive stale detection.
- What happens when an individual asset URL fails to fetch (404, timeout, bad URL) during asset resolution? The entire record containing that asset reference is skipped; its count is added to the job's asset-errors counter. The job continues processing remaining records and completes with a non-zero asset-errors count.
- What does the progress display show before the worker has set `records total`? The UI shows the processed count only (e.g., "12 records exported") without a percentage or progress bar until the total is populated by the worker.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The datasource page MUST include an "Export & Destination" block displayed inline below the Data Preview section in the same scrollable view; this block contains the sink selector, asset resolution toggle, and Export button; no navigation away from the page is required to initiate an export
- **FR-002**: Users MUST be able to select any datasink defined in the service YAML configuration as the export destination from a dropdown; datasinks are administrator-configured and available to all users
- **FR-003**: Users MUST be able to select a destination dataset name from a list fetched via the datasink's list-datasets endpoint, or enter a new name manually
- **FR-004**: Users MUST be able to toggle asset resolution mode on or off before initiating an export
- **FR-005**: When asset resolution is enabled, users MUST be able to: (a) select an asset datasink from the YAML-configured list; the asset dataset name is auto-derived as `{destination_dataset_name}_assets` and displayed as a read-only label in the UI; (b) review and confirm a list of asset URL fields to resolve, pre-populated by auto-detection using field name conventions (names in: `url`, `file_url`, `image_url`, `asset_url`, `thumbnail_url`, `media_url`; or any field name ending in `_url`, `_uri`, or `_path`) and URL pattern presence (values matching `^https?://`) in sample data from the datasource schema; (c) optionally specify an asset URL prefix that is prepended to field values before fetch (for sources that store relative paths or resource IDs rather than full URLs)
- **FR-006**: The system MUST capture the active datasource source type (`connection` or `system`), its identifier (DB id or config name), and all applied filters at export initiation time and store them in the export job; both user-created connections and administrator-defined system sources are valid export sources; the Data Preview table serves as the user-visible representation of records to be exported
- **FR-007**: Pressing Export MUST create an export job record and hand it off to a background worker container for processing
- **FR-008**: The background worker MUST, before processing the first batch, perform a count query against the datasource to set the job's `records total`; it then processes records in batches and updates the progress counter after each batch; progress updates serve as heartbeats; when a batch takes longer than a configurable keep-alive interval (default: 2 minutes), the worker MUST emit a keep-alive update (unchanged counters) to prevent the stale-detection sweep from marking the job as failed
- **FR-009**: The UI MUST display export progress by polling the jobs status endpoint at a default 3-second interval (configurable via service settings); when `records total` is null (worker not yet started), only the processed count is shown; once total is set, both count and percentage are shown; no manual refresh is required
- **FR-010**: The system MUST provide a "Jobs" tab in the main navigation showing all export jobs (past and present) with their status, source, destination, progress, and timestamps; this tab is separate from the datasource browser page
- **FR-011**: The datasink subsystem MUST define a base abstract class that all sink implementations extend, exposing a standard write interface
- **FR-012**: The system MUST define a unified datasink write protocol based on the dataset-mock service API with three operations: list-datasets (enumerate available destination datasets), create-dataset (create a named dataset before the first write), and post-file (write a single record or file to a dataset); all service-backed sinks MUST implement this protocol; the worker calls create-dataset for both the destination dataset and the auto-derived assets dataset (`{name}_assets`) before posting any files
- **FR-013**: The system MUST include a Dataset Mock Service sink that implements the unified write protocol natively against the dataset-mock service
- **FR-014**: The system MUST include an Annotator Mock Service sink that implements the unified write protocol via an adapter layer, translating calls to the annotator-mock service's native API
- **FR-015**: The system MUST include a Local ZIP Archive sink that packages exported records as individual files within a ZIP archive; each file's name is determined by a user-defined filename template configured on the datasink connection, with field references resolved against the discovered datasource schema (e.g., `{id}_{timestamp}.json`); when a template field cannot be resolved for a given record, the file name falls back to a content hash of that record
- **FR-016**: The system MUST include a Local JSONL sink that writes one JSON object per line for each exported record, applicable only when records are JSON-serializable
- **FR-017**: Export jobs MUST support status transitions: pending → running → completed / failed
- **FR-018**: The Jobs tab MUST provide a Retry action on failed jobs; triggering it creates a new export job pre-filled with the failed job's settings (datasource, filters, datasink, dataset name, asset resolution configuration); the original failed job remains in the list unchanged
- **FR-019**: Each export job MUST record the initiating user's identity and their organization (`org_id` and `user_id` extracted from the `X-GROUP-ID` request header)
- **FR-020**: The jobs list endpoint MUST enforce role-based visibility: `user` role returns only the caller's own jobs; `org_admin` role returns all jobs within the caller's org; `super_admin` role returns all jobs across all orgs
- **FR-021**: The system MUST reject export job creation requests that would exceed the per-org concurrent job limit (default: 5), returning a clear error message; the limit MUST be configurable via service settings
- **FR-022**: The system MUST automatically purge completed and failed export jobs after a configurable TTL (default: 7 days); running and pending jobs are never purged by TTL
- **FR-023**: The export pipeline MUST expose structured observability metrics (following the service's established metrics format) covering:
  - Export jobs created, completed, and failed counts (labelled by org and sink type)
  - Active export jobs count (labelled by org)
  - Records exported per second throughput (labelled by sink type)
  - Asset resolution success and failure counts
  - Per-org concurrent job count

### Key Entities

- **Export Job**: Represents a single export operation. Attributes: id, org_id, user_id (both extracted from `X-GROUP-ID` header at creation time), datasource type (`connection` for user-created DB connections or `system` for YAML-configured system sources), datasource ref (DB connection id when type=`connection`; config name when type=`system`), datasource filter snapshot (query, date range), datasink name (references a YAML-configured datasink), destination dataset name, asset resolution enabled flag, asset url fields list (user-confirmed list of field names to resolve; optional), asset url prefix (optional string prepended to field values before fetch; empty means use values as-is), asset datasink name (optional; when set, assets are written here), asset dataset name (auto-derived as `{destination_dataset_name}_assets`; only present when asset resolution enabled), status, records total, records processed, records skipped, asset errors (count of records skipped due to asset fetch failure), error message, created at, started at, completed at
- **Datasink**: A named export destination defined in the service YAML configuration by an administrator. Attributes: name, type (dataset-mock, annotator-mock, local-zip, local-jsonl), connection URL or path, credentials, filename template (ZIP sink only — field references drawn from datasource schema, e.g. `{id}_{timestamp}.json`; empty means hash fallback always applies). Datasinks are global — available to all users. Datasink services enforce their own access control; databridge will pass the user's JWT token to the datasink in a future phase.
- **Datasink Base Class**: Abstract interface that all concrete sink implementations must extend. Operations: list available destination datasets, create a named dataset, write a batch of records to a dataset, finalise/close the sink, and health-check (ping). Service-backed sinks implement these operations directly; sinks whose native API differs implement them via an internal adapter layer.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can initiate an export without leaving the datasource page: the Export & Destination block is inline below the Data Preview (which doubles as export preview); the interaction path is scroll → configure → press Export (3 steps or fewer)
- **SC-002**: Export progress updates are visible in the UI within 6 seconds of the worker processing each batch (worst-case: one poll interval plus one round-trip); achieved via client polling at a default 3-second interval (configurable via service settings)
- **SC-003**: Users can view all export jobs (active and historical) in a single list without navigating away from the main interface
- **SC-004**: Adding a new datasink type requires only implementing the abstract base class — no changes to export job orchestration or UI sink-type dispatch
- **SC-005**: Export jobs run entirely in the background; the main UI and API remain responsive during active exports
- **SC-006**: A 10,000-record export to a local JSONL sink completes within 2 minutes under normal operating conditions
- **SC-007**: A stalled export job (no progress update or keep-alive signal) is automatically transitioned to "failed" within 15 minutes (default stale timeout, configurable); workers processing slow batches emit a keep-alive at a configurable interval (default: 2 minutes) to prevent false-positive stale detection
- **SC-008**: Attempting to exceed the per-org concurrent job cap (default: 5) results in an immediate rejection with an informative error; the cap is configurable via service settings
- **SC-009**: Completed and failed export jobs are automatically removed after 7 days (default TTL, configurable); running and pending jobs are never purged by TTL
- **SC-010**: The export pipeline exposes structured metrics covering job lifecycle (created/completed/failed by org and sink type), active job count, records-per-second throughput, asset resolution outcomes, and per-org concurrency; all metrics are observable without code changes when new sink types are added

## Assumptions

- Datasinks are defined in the service YAML configuration by an administrator and served read-only to all users; there is no UI for creating or editing datasinks
- The feature branch (`001-datasink-export`) uses a sequential feature-branch counter; the change directory (`002-datasink-export`) uses a sequential change-set counter; these two counters are intentionally independent
- The asset resolution feature only fetches and stores asset binaries; it does not transform or process asset content
- The worker container has read access to the same datasource adapters as the main service
- For local sinks (ZIP, JSONL), the worker container writes to a shared volume accessible by the operating environment
- Export parameter extensions (masking, sampling, webhooks) are explicitly out of scope for this phase and will not be designed or stubbed here
- The system does not support export cancellation in this phase; running jobs run to completion or failure
- Datasink definitions are loaded from YAML config at service startup (analogous to system sources in Phase 1); they are not stored in the database
- JWT token passthrough from databridge to datasink services is out of scope for this phase; datasink access control enforcement is a future concern
- Export pipeline metrics follow the service's established Prometheus counter + histogram convention (per the project constitution)
- Auth context is conveyed via the `X-GROUP-ID` header containing `<org_id/user_id>`; the service does not issue or validate tokens — it trusts this header as set by the upstream gateway
- Three roles are recognized: `super_admin`, `org_admin`, `user`; role is conveyed via the `X-Role` header (`SUPER_ADMIN` / `ORG_ADMIN` / `USER`); `X-GROUP-ID` is split on the first `/` into `org_id` (left) and `user_id` (right)
- The worker container is a separate process/container running as `python -m worker`; it communicates with the API via an ARQ job queue backed by Redis (see plan.md §Key Design Decisions #1)
- JSONL export silently skips records that cannot be serialized to JSON and tracks them in the skipped counter
- The jobs list is paginated server-side for performance; default page size is 20 jobs
