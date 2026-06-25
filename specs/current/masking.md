# Data Masking — Implemented Contract

**Date**: 2026-06-03 | **Branch**: `002-browser-ui-redesign`

## Overview

Data masking applies field-level transformations to each record before it is written to the sink. Rules are defined per export job and applied by the ARQ worker.

## MaskingRule Model

```python
class MaskingAction(str, Enum):
    mask   = "mask"    # Replace value with "***"
    hash   = "hash"    # SHA-256 hex digest of the string value
    drop   = "drop"    # Remove field from record entirely
    redact = "redact"  # Replace value with "[REDACTED]"

class MaskingRule(BaseModel):
    field_path: str   # dot-path, e.g. "payload.user_id"
    action: MaskingAction
```

## Applying Rules

Rules are applied in order via `apply_masking(record, rules)` in `src/databridge/export/masking.py`. The function:
- Returns a deep copy of the record (original is not mutated)
- Resolves dot-paths (e.g. `payload.user_id` → `record["payload"]["user_id"]`)
- Silently skips rules where the field path doesn't resolve

## PII Auto-Detection

`GET /api/v1/connections/{id}/pii-fields` returns candidate PII fields using a name-pattern heuristic matching: `email`, `phone`, `ssn`, `password`, `ip`, `user_id`, `token`, `secret`, `card`.

## Storage

`masking_rules` column (JSONB NOT NULL DEFAULT '[]') on `export_jobs` table (migration 0003).
