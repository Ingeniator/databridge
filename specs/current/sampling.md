# Sampling Strategy — Implemented Contract

**Date**: 2026-06-03 | **Branch**: `002-browser-ui-redesign`

## Overview

Sampling reduces the number of records written to the sink by applying a statistical strategy. Configured per export job; applied by the ARQ worker.

## SamplingConfig Model

```python
class SamplingMethod(str, Enum):
    random     = "random"      # Uniform random sample
    systematic = "systematic"  # Every Nth record
    stratified = "stratified"  # Proportional by target column value

class SamplingConfig(BaseModel):
    method: SamplingMethod = SamplingMethod.random
    target_column: str | None = None   # Required for stratified
    ratio_or_size: float               # <1.0 = ratio; >1.0 = absolute count; 1.0 = all
```

## Strategy Behaviour

| Strategy | Behaviour |
|---|---|
| random | `random.random() < ratio` per record; `ratio > 1.0` keeps first N |
| systematic | Every Nth record where N = 1/ratio; `ratio >= 1.0` keeps all |
| stratified | Per-group quota; `ratio > 1.0` means N per group |

## Storage

`sampling_config` column (JSONB nullable) on `export_jobs` table (migration 0003).
