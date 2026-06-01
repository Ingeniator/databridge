# Feature Specification: Datasource Connection Management

**Feature Branch**: `001-datasource-connection-sync`

**Created**: 2026-06-01

**Status**: Draft

**Input**: User description: "i want to build service that will allow user to sync datasource and datasink in automated way. as a source could be S3, clickhouse, trino, langfuse. as sink dataset-mock or annotator-mock service. user should be able to add custom connections and pass its own credentials to store securely and schedule sync job. but to define sync job we need to build ui interface where user will prepare and preview such job details. as the first step i want to build backend and ui to connect datasource."

---

## Overview

This is **Phase 1** of the databridge service: a connection management system that lets users register, test, and browse data from external sources (S3, ClickHouse, Trino, Langfuse) and sinks (annotator-compatible dataset services). Users supply their own credentials, which are stored securely server-side. A browser UI provides a self-service interface for adding connections and previewing data before any sync jobs are defined.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Add and Test a New Connection (Priority: P1)

A user wants to connect to their ClickHouse instance to later use it as a data source in a sync job. They open the databridge UI, click "Add Connection", fill in the host URL, username, password, and a friendly label, then hit "Test Connection". The service verifies the credentials reach the server without error and shows a green confirmation. The connection is saved and appears in their connections list immediately.

**Why this priority**: This is the atomic unit all other features build on. Without a saved, validated connection, nothing else in the system can function. It is the entry point for every user.

**Independent Test**: Can be fully tested by opening the UI, adding a ClickHouse connection with known-good credentials, and verifying it appears in the list with a "reachable" status badge. No sync jobs or sinks needed.

**Acceptance Scenarios**:

1. **Given** no connections exist, **When** the user submits a valid ClickHouse connection form, **Then** the connection is saved, never exposes the password in any subsequent response, and is immediately listed on the connections page.
2. **Given** a filled connection form, **When** the user clicks "Test Connection" before saving, **Then** the service attempts to reach the backend and shows a success or failure message within 5 seconds.
3. **Given** an unreachable backend URL, **When** the user submits the form, **Then** the save succeeds (credentials are stored) but the status badge shows "unreachable" until the next successful ping.
4. **Given** a saved connection, **When** the user deletes it, **Then** it disappears from the list and credentials are permanently removed from storage.

---

### User Story 2 — Browse and Preview Data from a Saved Connection (Priority: P2)

A user has a saved Langfuse connection and wants to verify the data looks right before scheduling any sync. They select the connection from the list and trigger schema discovery to see what fields are available. They can also enter a time range and configure optional keyword filter, and see a live preview of matching records in a table.

**Why this priority**: Data preview gives users confidence that their connection is pointed at the right data before committing to a sync schedule. It validates both credentials and data shape.

**Independent Test**: Can be fully tested by selecting an existing connection, running a search with a short time window, and confirming records appear in the results table. Schema discovery can be tested separately by clicking the "Discover Schema" button.

**Acceptance Scenarios**:

1. **Given** a saved S3 connection, **When** the user selects it and clicks "Preview", **Then** up to 50 records are shown in a table within 10 seconds.
2. **Given** a Langfuse connection with data, **When** the user applies a keyword filter, **Then** only matching records appear in the results.
3. **Given** any saved source connection, **When** the user triggers schema discovery, **Then** a list of field names with inferred types and example values is displayed.
4. **Given** credentials that became invalid after saving, **When** the user triggers a preview, **Then** a clear error message is shown with a link to edit the connection.

---

### User Story 3 — Manage Multiple Connections Across Types (Priority: P3)

A user manages connections to several different backends — a production ClickHouse cluster, a staging Trino warehouse, an S3 bucket for raw logs, and a Langfuse cloud account. They can view all connections in a list, see each connection's type and label, re-test any of them, edit the label or credentials, and delete connections they no longer need.

**Why this priority**: Multi-connection management is essential for production use but secondary to the core add-and-test flow. A single working connection validates the entire system.

**Independent Test**: Can be tested by creating three connections of different types, renaming one, updating credentials on another, and deleting the third — then verifying the list reflects each change independently.

**Acceptance Scenarios**:

1. **Given** three saved connections of different types, **When** the user views the connections list, **Then** each shows its label, type badge, connection URL, creation date, and last-ping status.
2. **Given** a saved connection, **When** the user updates the label, **Then** the new label is immediately reflected without requiring a re-test.
3. **Given** a saved connection, **When** the user updates credentials, **Then** the old credentials are replaced in encrypted storage and the connection status is reset to "untested".
4. **Given** a connection that is in use by a sync job (future feature), **When** the user attempts to delete it, **Then** deletion is blocked with a message explaining which jobs reference it.

---

### Edge Cases

- What happens when a connection URL passes validation but the backend is temporarily down? The connection is saved; health status shows "unreachable" and updates automatically on next ping.
- How does the system handle two connections with the same label? Labels are user-visible only; uniqueness is not enforced — only the server-generated ID is a unique key.
- What if the user provides malformed credentials (e.g., wrong S3 key format)? The form validates required fields client-side (non-empty, URL format); server-side validation returns a descriptive error without saving.
- What happens if the encryption key is rotated? Existing credentials cannot be decrypted until re-entered by the user. Key rotation tooling is out of scope for this phase.
- What if a user has no connections yet? The connections page shows an empty state with a call-to-action to add the first connection. System sources still appear in the "System Sources" section regardless.
- What if the `config.yaml` file is missing at startup? The service MUST fail fast with a clear error naming the missing file and its expected path.
- What if a `vault:KEYNAME` reference in `config.yaml` cannot be resolved? The service MUST fail fast at startup, naming the unresolvable key, before accepting any traffic.
- What if a system source becomes unreachable after startup? Its status updates to "unreachable" on the next ping, the same as user-owned connections. No restart is required.
- What happens if an administrator renames a system source in `config.yaml`? The source's ID changes (it is a deterministic hash of the name). Any client URL referencing the old ID returns 404 after restart. Treat a rename as removing the old source and adding a new one.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to create a named connection for each supported source type: S3, ClickHouse, Trino, Langfuse.
- **FR-002**: Users MUST be able to create a named connection for each supported sink type: annotator-compatible dataset service (any service implementing the `POST /api/v0/datasets` and `POST /api/v0/datasets/{id}/files` contract).
- **FR-003**: The system MUST encrypt all credential fields (passwords, secret keys, access tokens) at rest using symmetric encryption before writing to the database; plaintext credentials MUST NOT appear in any API response.
- **FR-004**: Each user-owned connection MUST be scoped to the authenticated user — a user MUST NOT be able to view, edit, or delete another user's connections.
- **FR-005**: The system MUST expose a connection health-check endpoint that tests reachability using the stored credentials and returns a reachable/unreachable status within 10 seconds.
- **FR-006**: The system MUST expose a data preview endpoint for source connections that returns up to 50 records matching an optional query string and time range.
- **FR-007**: The system MUST expose a schema discovery endpoint for source connections that returns a list of available fields, their inferred types, and example values.
- **FR-008**: Users MUST be able to update the label or credentials of an existing user-owned connection without deleting and re-creating it.
- **FR-009**: Users MUST be able to delete a user-owned connection; deletion MUST be blocked if the connection is referenced by an existing sync job (future feature), with a clear error explaining the block.
- **FR-010**: The browser UI MUST allow users to perform all connection CRUD operations (create, list, view, edit, delete) and run a health check without leaving the page.
- **FR-011**: The browser UI MUST show a live data preview and schema view for any saved source connection.
- **FR-012**: The system MUST return a server-generated unique ID for each connection on creation; this ID is used for all subsequent operations on that connection.
- **FR-013**: The connections list endpoint MUST return all connections owned by the caller, ordered by creation date descending, AND all system sources (ordered by config file declaration order). Each item MUST carry a `system` boolean field.
- **FR-014**: The system MUST support at least the following credential shapes per type:
  - **S3**: endpoint URL, bucket, region, access key ID, secret access key, key prefix
  - **ClickHouse**: host URL, database, table, username, password
  - **Trino**: host URL, catalog, schema, username, password
  - **Langfuse**: host URL, public key, secret key
  - **Dataset sink**: base URL, API token (optional)
- **FR-015**: All service configuration MUST be read from a YAML file. The file path is resolved in priority order: (1) `DATABRIDGE_CONFIG` env var if set, (2) `config.yaml` two directories above `config.py` (local dev), (3) `config.yaml` in the working directory (production default). Two environment variables are used: `DATABRIDGE_CONFIG` (config file path) and `VAULT_SECRETS_PATH` (Vault sidecar file path, default `/vault/secrets/env`). No other environment variables are used for application configuration.
- **FR-016**: The configuration YAML MUST support two secret-injection mechanisms, applied before YAML parsing: (1) `vault:KEYNAME` references resolved from the Vault sidecar file at `VAULT_SECRETS_PATH` (default `/vault/secrets/env`); (2) `$VAR` / `${VAR}` environment variable expansion via `os.path.expandvars`. Both mechanisms may be mixed in the same file. Any unresolvable `vault:KEYNAME` reference MUST cause the service to fail fast with a clear error message naming the missing key.
- **FR-017**: Administrators MUST be able to define predefined system datasources in the YAML configuration. System sources are available to all users as read-only sources; they cannot be created, updated, or deleted via the API.
- **FR-018**: The connections list endpoint (`GET /api/v1/connections`) MUST return both user-owned connections and system sources in a single response. System source items have `system: true` and `role: source`. Ping, preview, and schema discovery MUST work against system sources using the credentials from the YAML config (decrypted at request time from the in-memory resolved config; never stored in the database).
- **FR-019**: The browser UI MUST display system sources in a distinct **"System Sources"** section, visually separate from the user's own connections. System sources are read-only: users may run ping, preview, and schema discovery but cannot edit or delete them.
- **FR-020**: The service MUST expose three health probe endpoints using a three-state per-component model (`ok` / `degraded` / `disabled`):
  - `GET /livez` — always returns 200 `{"status": "ok"}`; never checks dependencies.
  - `GET /ready` — checks all enabled components concurrently; returns 200 `{"status": "ok", "components": {...}}` when all are `ok`; returns 503 when any is `degraded`. Components in Phase 1: `db` (asyncpg pool) plus one entry per configured system source.
  - `GET /health` — same component sweep as `/ready` plus a `version` field and a `details` dict containing error messages for any degraded component. Used for dashboards, not k8s probes.
  - `disabled` components (not configured) MUST be excluded from the `ok`/`degraded` determination and MUST NOT be pinged.

### Key Entities

- **Connection**: A user-owned record linking a friendly label, a backend type, a non-sensitive connection URL, and encrypted credentials. Scoped by user identity. Can represent either a source or a sink. `system: false`.
- **SystemSource**: A read-only datasource defined in the service YAML config. Available to all users. Identified by a deterministic UUID v5 derived from its `name` field in the config — renaming a source changes its ID. Credentials are resolved from the YAML (including vault references) at startup and held in memory; they are never written to the database. `system: true`, `role: source`.
- **ConnectionStatus**: The result of the most recent health-check ping — reachable, unreachable, or untested. For user-owned connections, persisted in the database. For system sources, held in memory only (reset on service restart).
- **PreviewResult**: A transient, paginated list of records returned from a source connection for display purposes. Never persisted.
- **SchemaField**: A single discovered field with a name (dot-notation path), inferred data type, and an example value. Part of a transient schema discovery response.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can add a new connection of any supported type, test it, and see it appear in their connections list in under 30 seconds end-to-end.
- **SC-002**: Credential fields are never returned in plaintext by any API response — verifiable by inspecting all GET and PATCH responses for password, secret key, and access token fields.
- **SC-003**: A data preview for any saved source connection returns results within 10 seconds for datasets up to 10,000 records.
- **SC-004**: Schema discovery for any saved source connection completes within 15 seconds and returns at least the top-level fields present in the sampled data.
- **SC-005**: A user with 10 saved connections can view, search, and manage all of them from a single page without any page reloads.
- **SC-006**: Connection health checks complete within 5 seconds under normal network conditions and clearly distinguish between "reachable", "unreachable", and "untested" states.
- **SC-007**: Deleting a connection permanently removes its encrypted credentials from storage — verifiable by confirming the connection ID no longer exists after deletion.
- **SC-008**: System sources defined in `config.yaml` appear in the "System Sources" section on first page load without any user action; they are available for preview and schema discovery immediately.

---

## Assumptions

- Users are authenticated via an `X-Group-ID` header forwarded by a reverse proxy (nginx), consistent with the dataimporter authentication pattern. No new authentication system is introduced.
- The databridge service will use PostgreSQL for persistent connection storage, following the architecture outlined in the dataimporter new-service-brief.
- The annotator-mock service running in the same ai-suite environment is the reference implementation of the supported sink contract (`POST /api/v0/datasets`, `POST /api/v0/datasets/{id}/files`).
- Connection URL validation only checks format and that the host is reachable on ping — full SSRF prevention via an admin-managed allowlist is deferred to a later phase.
- Mobile layout and accessibility audit are out of scope for Phase 1.
- The browser UI will follow the same technology pattern as dataimporter: a vanilla-JS SPA served by the FastAPI backend, styled with Tailwind CSS from CDN.
- Sync job scheduling (cron, filters, run history) is explicitly out of scope for this phase and will be addressed in Phase 2.
- At most one schema discovery sample call is issued per user interaction — there is no background schema refresh.
- All service configuration (database URL, encryption key, system sources, server settings) is supplied via a YAML config file. Two env vars are used: `DATABRIDGE_CONFIG` (config file path) and `VAULT_SECRETS_PATH` (Vault sidecar path, default `/vault/secrets/env`). Secret values in the YAML support both `vault:KEYNAME` references and `$VAR` env-var expansion. The Settings object is validated strictly at startup — unknown keys raise an error. Key rotation is out of scope for Phase 1.

---

## Clarifications

### Session 2026-06-01

- Q: What can users do with predefined system sources in the UI? → A: Shown in a separate "System Sources" section; read-only (no edit/delete); ping, preview, and schema discovery available.
- Q: How should the service locate its configuration file? → A: Fixed default `config.yaml` in the working directory; optional `DATABRIDGE_CONFIG` environment variable to override the path.
- Q: How should the API expose system sources alongside user connections? → A: `GET /api/v1/connections` returns both, with a `system: bool` field on each item. System sources have `system: true` and `role: source`.

### Session 2026-06-01 (continued)

- Q: How should the Vault sidecar file path be specified? → A: ~~Path in `server.vault_secrets_path` YAML field~~ **Revised**: `VAULT_SECRETS_PATH` is a second env var (infrastructure-level, alongside `DATABRIDGE_CONFIG`), matching the `configuration.md` spec. FR-015 and FR-016 updated accordingly.
- Q: If an administrator renames a system source in `config.yaml`, should its API ID change? → A: Yes — ID is a deterministic UUID v5 of the source name; rename = new ID. Document as equivalent to removing the old source and adding a new one.

### Session 2026-06-01 (specs/current integration)

- Q: Is `VAULT_SECRETS_PATH` an env var or a YAML field? → A: Env var (alongside `DATABRIDGE_CONFIG`), matching `configuration.md`. FR-015 and FR-016 updated; previous answer revised.
- Q: Should `/ready` and `/health` use the richer per-component format from `service-metrics-health.md`? → A: Yes — three-state model (`ok`/`degraded`/`disabled`) with per-component dict. FR-020 added.
