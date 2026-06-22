# Multi-tenant Data Authorization

**Status**: Proposed  
**Date**: 2026-06-22

## Problem

Access to a datasource connection is currently binary: if a connection is configured, any authenticated user can retrieve any data from it. There is no server-enforced row-level restriction. In a multi-tenant deployment where a single datasource (e.g. a shared ClickHouse table) holds data for multiple tenants, a `row_filter` must be transparently injected into every query so a user can only ever see their own tenant's rows.

## Approach

Add a `row_filter` template field to the `connections` table. The template is authored by an admin at connection-creation time and resolved at request time from the caller's auth context. The resolved SQL fragment is injected as a mandatory AND condition before any user-supplied filter, so it cannot be bypassed.

## Template Syntax

The `row_filter` value is a SQL fragment with `{{variable}}` placeholders:

```
org_id = '{{org_id}}'
```

Available variables (sourced from `AuthContext` in `auth.py`):

| Variable | Source |
|---|---|
| `{{org_id}}` | `AuthContext.org_id` |
| `{{user_id}}` | `AuthContext.user_id` |
| `{{role}}` | `AuthContext.role` |

Values are single-quote–escaped before substitution to prevent SQL injection.

## Resolution

Resolution happens at the **route layer** (`routes/connections.py`) where auth context is already present. A helper function:

```python
def resolve_row_filter(template: str | None, auth: AuthContext) -> str | None:
    if not template:
        return None
    return (template
        .replace("{{org_id}}", auth.org_id.replace("'", "''"))
        .replace("{{user_id}}", auth.user_id.replace("'", "''"))
        .replace("{{role}}", auth.role.replace("'", "''")))
```

The resolved string is set on the adapter before calling any query method. SQL adapters (`ClickHouseConnectionAdapter`, `TrinoConnectionAdapter`) prepend it as the first condition in the WHERE clause — before any user-provided filter — so user filters can narrow but never escape the tenant scope.

## Export Jobs

The ARQ worker runs without an HTTP request context, so it cannot resolve the template at execution time. Instead:

- When a job is created (route has auth), `row_filter` is resolved immediately and stored as a **server-set field** on the `export_jobs` row (e.g. `resolved_row_filter TEXT`).
- This field is **not** part of the client request body — it is set exclusively by the server.
- The worker reads `resolved_row_filter` from the job and passes it through to adapter calls unchanged.

## Changes Required

### Database

- `connections` table: add `row_filter TEXT NULL`
- `export_jobs` table: add `resolved_row_filter TEXT NULL` (server-set, never user-supplied)
- Two new Alembic migrations

### Models

- `ConnectionCreate` / `ConnectionPatch`: add `row_filter: str | None = None`
- `ConnectionRow` dataclass: add `row_filter: str | None`
- `ConnectionResponse`: add `row_filter: str | None = None`
- `ExportJob` / `ExportJobResponse`: add `resolved_row_filter: str | None = None`

### Adapters (`adapters.py`)

- `BaseAdapter`: add `_row_filter: str | None = None`
- Each SQL adapter (`ClickHouseConnectionAdapter`, `TrinoConnectionAdapter`): prepend `self._row_filter` as the first entry in `conditions` inside `preview`, `count`, and `fetch_page`
- No change to the `ConnectionAdapter` Protocol

### Routes (`routes/connections.py`)

- Add `resolve_row_filter(template, auth)` helper
- After building each adapter (for user connections), call `resolve_row_filter` and set `adapter._row_filter`
- Pass `resolved_row_filter` to `insert_export_job` when creating jobs

### Worker (`export/worker.py`)

- Read `resolved_row_filter` from the job record
- Set it on the adapter before the sweep loop

## Limitations

- **Langfuse**: API-based, no SQL WHERE clause. `row_filter` does not apply. Tenant scoping for Langfuse requires a separate mechanism (e.g. project-level API keys per tenant).
- **S3**: DuckDB SQL is used, so `row_filter` is technically injectable, but only works if the filtered column exists in the parquet/CSV files. Behaviour is undefined if the column is absent.
- **System sources** (configured via `config.yaml`): do not support `row_filter` in this design — they are admin-configured and assumed pre-scoped.
