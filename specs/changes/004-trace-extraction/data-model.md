# Data Model: Field Extraction Stage

**Phase 1 output** | **Date**: 2026-07-13

---

## 1. Pydantic Model Extensions (`src/databridge/export/models.py`)

### 1.1 `ExportJob` / `ExportJobCreate` / `ExportJobResponse` — new fields

Added to all three, same shape as the `asset_resolution`/`asset_url_fields` pair:

```python
field_extraction: bool = False
field_extraction_path: str = ""
```

### 1.2 Validation

```python
from pydantic import model_validator

class ExportJobCreate(BaseModel):
    ...
    field_extraction: bool = False
    field_extraction_path: str = ""

    @model_validator(mode="after")
    def _validate_field_extraction(self) -> "ExportJobCreate":
        if self.field_extraction and not self.field_extraction_path.strip():
            raise ValueError("field_extraction_path is required when field_extraction is enabled")
        return self
```

`POST /api/v1/export-jobs` returns `422` (standard FastAPI/Pydantic validation error shape) when this fires.

### 1.3 New request/response models (preview endpoint)

Mirrors `AssetResolutionTestRequest` / `AssetUrlTestResult` / `AssetResolutionTestResponse`:

```python
class FieldExtractionTestRequest(BaseModel):
    field_path: Annotated[str, Field(min_length=1, max_length=255)]


class FieldExtractionTestResult(BaseModel):
    resolved: bool
    value_preview: str | None = None   # json.dumps(value)[:500] when resolved
    error: str | None = None           # e.g. "field not found", "value is not JSON-parseable"


class FieldExtractionTestResponse(BaseModel):
    results: list[FieldExtractionTestResult]
```

---

## 2. Extraction Function (`src/databridge/export/extraction.py`, new module)

```python
from __future__ import annotations

import json
from typing import Any

_MISSING = object()


def resolve_field_path(container: Any, parts: list[str]) -> Any:
    """Read-only counterpart to masking._apply_at_path's descent: walks `parts`
    inside `container`, transparently json.loads-ing any string container
    encountered along the way. Returns _MISSING if the path doesn't resolve.
    """
    node = container
    if isinstance(node, str):
        try:
            node = json.loads(node)
        except (json.JSONDecodeError, ValueError):
            return _MISSING

    if not parts:
        return node

    key = parts[0]
    if not isinstance(node, dict) or key not in node:
        return _MISSING

    if len(parts) == 1:
        return node[key]
    return resolve_field_path(node[key], parts[1:])


def extract_field_value(record: dict, field_path: str) -> Any | None:
    """Resolve field_path in record and return usable extracted content, or
    None if the field is missing or its value isn't usable (see FR-007: only
    dict/list values, native or JSON-string-encoded, count as usable).
    """
    resolved = resolve_field_path(record, field_path.split("."))
    if resolved is _MISSING:
        return None
    if isinstance(resolved, (dict, list)):
        return resolved
    if isinstance(resolved, str):
        try:
            parsed = json.loads(resolved)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, (dict, list)) else None
    return None
```

`masking.py`'s `_apply_at_path` is refactored to call `resolve_field_path` for the read/descend portion, keeping the mutate-in-place logic local to masking. This removes the duplicated JSON-string-transparent traversal (Constitution §IV DRY).

---

## 3. Worker Integration (`src/databridge/export/worker.py`)

Record loop gains one stage, inserted between sampling and masking:

```python
field_extraction = job_resp["field_extraction"]
field_extraction_path = job_resp["field_extraction_path"] or ""

...

for record in records:
    if sampling_buffer is not None:
        if not sampling_buffer.feed(record):
            records_skipped += 1
            SAMPLING_RECORDS_DROPPED.labels(org_id=org_id).inc()
            continue

    if field_extraction:
        from databridge.export.extraction import extract_field_value
        extracted = extract_field_value(record, field_extraction_path)
        if extracted is None:
            records_skipped += 1
            EXPORT_FIELD_EXTRACTION_FAILED.inc()
            continue
        record = extracted
        EXPORT_FIELD_EXTRACTION_SUCCESS.inc()

    if masking_rules:
        ...  # unchanged, now operates on `record` post-extraction
```

No other stage's code changes — `sampling_buffer.feed`, `apply_masking`, and `resolve_assets` all continue to receive whatever `record` currently holds, which is either the original envelope (extraction off) or the extracted payload (extraction on).

---

## 4. Database Migration (`0008_field_extraction.py`)

```python
"""add field extraction columns to export_jobs

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-13
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE export_jobs ADD COLUMN field_extraction BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE export_jobs ADD COLUMN field_extraction_path TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE export_jobs DROP COLUMN IF EXISTS field_extraction_path")
    op.execute("ALTER TABLE export_jobs DROP COLUMN IF EXISTS field_extraction")
```

Plain scalar columns (unlike `masking_rules`/`sampling_config`, which are `JSONB`) — no serialization helper needed in `db.py`, just direct `row["field_extraction"]` / `row["field_extraction_path"]` reads and two extra positional params in the `INSERT`.

---

## 5. Metrics (`src/databridge/export_metrics.py`)

```python
EXPORT_FIELD_EXTRACTION_SUCCESS = Counter(
    "export_field_extraction_success_total",
    "Records successfully reduced to their extracted field value",
)
EXPORT_FIELD_EXTRACTION_FAILED = Counter(
    "export_field_extraction_failed_total",
    "Records skipped because the configured field extraction path did not resolve to usable content",
)
```

Unlabeled, matching `EXPORT_ASSET_RESOLUTION_SUCCESS`/`_FAILED`.

---

## 6. API Route (`src/databridge/routes/connections.py`)

```python
@router.post("/connections/{id}/test-field-extraction", response_model=FieldExtractionTestResponse)
async def test_field_extraction(
    id: UUID,
    body: FieldExtractionTestRequest,
    auth: AuthContext = Depends(get_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    system_sources: list[SystemSourceConfig] = Depends(get_system_sources),
) -> FieldExtractionTestResponse:
    ...  # resolve adapter exactly as test_asset_resolution does
    records = await adapter.preview("", None, None, limit=5)
    from databridge.export.extraction import extract_field_value
    results = []
    for rec in records:
        value = extract_field_value(rec, body.field_path)
        if value is None:
            results.append(FieldExtractionTestResult(resolved=False, error="field not found or not JSON-parseable content"))
        else:
            results.append(FieldExtractionTestResult(resolved=True, value_preview=json.dumps(value)[:500]))
    return FieldExtractionTestResponse(results=results)
```

Same adapter-resolution branch (system source vs. DB connection) as `test_asset_resolution` — no new adapter capability needed, reuses `adapter.preview()`.

---

## 7. UI Test IDs (`browser.html` / `browser.js`)

New export-config block, sibling of the existing `#asset-resolution-*` block:

| Element | `data-testid` |
|---|---|
| Enable toggle | `field-extraction-toggle` |
| Field path input | `field-extraction-path-input` |
| Test button | `test-field-extraction-btn` |
| Results panel | `field-extraction-results` |
| Per-result row | `field-extraction-result-{n}` |

Job creation payload (`static/browser.js`, the object built for `POST /api/v1/export-jobs`) gains:

```js
field_extraction: document.getElementById('field-extraction-toggle')?.checked || false,
field_extraction_path: document.getElementById('field-extraction-path-input')?.value || '',
```

---

## 8. Key Entities Recap

- **Export Job Configuration**: `field_extraction: bool`, `field_extraction_path: str` — persisted alongside existing `asset_resolution`/`masking_rules`/`sampling_config` on the same `export_jobs` row.
- **Extracted Content**: not a persisted entity — a transient value computed per-record inside the worker loop by `extract_field_value`, replacing `record` for the remainder of that record's pipeline pass. A trace payload is one example of extracted content; any structured JSON value is valid.
