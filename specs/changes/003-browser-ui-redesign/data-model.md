# Data Model: Browser UI Redesign

**Branch**: `002-browser-ui-redesign` | **Date**: 2026-06-03

---

## 1. Updated Pydantic Models

### 1a. FilterSnapshot (updated)

```python
class FilterSnapshot(BaseModel):
    query: str = ""
    start: datetime | None = None
    end: datetime | None = None
    time_field: str | None = None   # NEW — schema field used for time range filtering
    limit: int = Field(default=50, ge=1, le=100_000)  # NEW — preview row limit
```

### 1b. MaskingRule (new)

```python
class MaskingAction(str, Enum):
    mask   = "mask"    # Replace with "***"
    hash   = "hash"    # SHA-256 hex digest
    drop   = "drop"    # Remove field from record entirely
    redact = "redact"  # Replace with "[REDACTED]"

class MaskingRule(BaseModel):
    field_path: Annotated[str, Field(min_length=1, max_length=255)]  # dot-path e.g. "payload.user_id"
    action: MaskingAction
```

### 1c. SamplingMethod (new)

```python
class SamplingMethod(str, Enum):
    random     = "random"      # Uniform random sample
    systematic = "systematic"  # Every Nth record
    stratified = "stratified"  # Proportional by target column value

class SamplingConfig(BaseModel):
    method: SamplingMethod = SamplingMethod.random
    target_column: str | None = None      # Required for stratified
    ratio_or_size: float = Field(gt=0.0)  # <1.0 → ratio (e.g. 0.10); >=1.0 → absolute count
```

### 1d. ExportJobCreate (updated)

```python
class ExportJobCreate(BaseModel):
    # — existing fields —
    datasource_type: Literal["connection", "system"]
    datasource_ref: str
    datasource_filter: FilterSnapshot = Field(default_factory=FilterSnapshot)
    datasink_name: str
    destination_dataset: Annotated[str, Field(min_length=1, max_length=120)]
    asset_resolution: bool = False
    asset_url_fields: list[str] = Field(default_factory=list)
    asset_url_prefix: str = ""
    asset_datasink_name: str | None = None
    # — new fields —
    masking_rules: list[MaskingRule] = Field(default_factory=list)
    sampling_config: SamplingConfig | None = None
    webhook_url: str | None = None       # POST target on job completion
    webhook_enabled: bool = False
```

### 1e. ExportJob + ExportJobResponse (updated)

Both gain the same four new fields as `ExportJobCreate`:
`masking_rules`, `sampling_config`, `webhook_url`, `webhook_enabled`.

### 1f. PreviewResponse (updated)

```python
class PreviewResponse(BaseModel):
    results: list[dict]
    total_count: int        # NEW — unbounded COUNT(*) for the current filter
    schema_fields: dict     # existing — field → {type, example}
```

### 1g. PiiFieldsResponse (new)

```python
class PiiFieldsResponse(BaseModel):
    candidate_fields: list[str]   # field paths matching PII name heuristic
```

---

## 2. Database Migration (0003)

New columns on `export_jobs`:

```sql
ALTER TABLE export_jobs
  ADD COLUMN masking_rules  JSONB NOT NULL DEFAULT '[]',
  ADD COLUMN sampling_config JSONB,                       -- nullable
  ADD COLUMN webhook_url    TEXT,                         -- nullable
  ADD COLUMN webhook_enabled BOOLEAN NOT NULL DEFAULT FALSE;
```

No index required; these columns are read once per job by the worker.

---

## 3. New API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/connections/{id}/pii-fields` | Returns candidate PII field names using name-pattern heuristic |

Existing endpoints changed:
- `POST /api/v1/connections/{id}/preview` — response gains `total_count` + respects `time_field` in body
- `POST /api/v1/export-jobs` — request body now accepts `masking_rules`, `sampling_config`, `webhook_url`, `webhook_enabled`
- `GET /api/v1/export-jobs`, `GET /api/v1/export-jobs/{id}` — response now includes those four fields

---

## 4. UI Component Tree

```
BrowserSPA
├── TopNav
│   ├── Brand (logo + title)
│   ├── NavTab[data-testid="nav-tab-import"] — "Data Import"
│   └── NavTab[data-testid="nav-tab-jobs"]   — "Jobs"
│
├── DataImportView [data-testid="data-import-view"]
│   │
│   ├── ConnectionTabBar [data-testid="connection-tab-bar"]
│   │   ├── ConnTab[data-testid="conn-tab-{id}"] × N  (label + type badge)
│   │   ├── AddConnectionTab [data-testid="add-connection-tab"]
│   │   └── SyncStatus
│   │       ├── LastSyncedLabel [data-testid="last-synced-label"]
│   │       └── HealthBadge [data-testid="health-badge"]
│   │
│   ├── RefineDatasetCard [data-testid="refine-dataset-card"]
│   │   ├── CardHeader
│   │   │   └── ClearAllBtn [data-testid="clear-all-btn"]
│   │   ├── SchemaDiscoverySection [data-testid="schema-discovery-section"]
│   │   │   ├── SchemaCollapseBtn [data-testid="schema-collapse-btn"]
│   │   │   ├── FieldChip × ≤3 [data-testid="schema-chip-{field}"]
│   │   │   ├── SchemaDescription
│   │   │   └── SchemaActions
│   │   │       ├── ColumnsPickerBtn [data-testid="columns-picker-btn"]
│   │   │       └── SchemaRefreshBtn [data-testid="schema-refresh-btn"]
│   │   └── FilterRow
│   │       ├── TimeRangeColumn
│   │       │   ├── TimeFieldBadge [data-testid="time-field-badge"]
│   │       │   └── TimeRangeSelect [data-testid="time-range-select"]
│   │       └── PredicateColumn
│   │           ├── PredicateInput [data-testid="predicate-filter-input"]
│   │           └── AdvancedFilterBtn [data-testid="filter-advanced-btn"]
│   │               └── AdvancedFilterPanel [data-testid="advanced-filter-panel"]
│   │                   └── FilterRuleRow × N [data-testid="filter-rule-{n}"]
│   │
│   ├── DataPreviewSection [data-testid="data-preview-section"]
│   │   ├── SectionHeader
│   │   │   ├── VisibilityBtn [data-testid="visibility-btn"]
│   │   │   ├── LimitBadge [data-testid="limit-badge"]
│   │   │   └── TotalRowsDisplay [data-testid="total-rows-display"]
│   │   ├── PreviewTable [data-testid="preview-table"]
│   │   │   └── DataRow × N [data-testid="preview-row-{n}"]
│   │   └── LoadMoreBtn [data-testid="load-more-btn"]
│   │
│   ├── DataMaskingCard [data-testid="data-masking-card"]
│   │   ├── MaskingToggle [data-testid="masking-toggle"]
│   │   ├── MaskingRulesTable [data-testid="masking-rules-table"]
│   │   │   └── MaskingRuleRow × N [data-testid="masking-rule-{n}"]
│   │   ├── AddMaskingRuleBtn [data-testid="add-masking-rule-btn"]
│   │   └── PiiAutoDetectionToggle [data-testid="pii-auto-detection-toggle"]
│   │
│   ├── SamplingStrategyCard [data-testid="sampling-strategy-card"]
│   │   ├── SamplingToggle [data-testid="sampling-toggle"]
│   │   ├── MethodSelect [data-testid="sampling-method-select"]
│   │   ├── MethodDescription [data-testid="sampling-method-desc"]
│   │   ├── TargetColumnInput [data-testid="sampling-target-column"]
│   │   └── RatioInput [data-testid="sampling-ratio"]
│   │
│   ├── ExportDestinationSection [data-testid="export-destination-section"]
│   │   ├── AssetResolutionToggle [data-testid="asset-resolution-toggle"]
│   │   ├── SinkSelect [data-testid="datasink-select"]
│   │   └── ExportBtn [data-testid="export-btn"]
│   │
│   └── WebhookConfigCard [data-testid="webhook-config-card"]
│       ├── WebhookToggle [data-testid="webhook-toggle"]
│       ├── WebhookUrlInput [data-testid="webhook-url-input"]
│       └── TestWebhookBtn [data-testid="test-webhook-btn"]
│
├── JobsView [data-testid="jobs-view"]
│   ├── JobsEmpty [data-testid="jobs-empty-msg"]
│   └── JobRow[data-testid="job-row-{id}"] × N
│       ├── JobStatus [data-testid="job-status-{id}"]
│       ├── JobSource [data-testid="job-source-{id}"]
│       ├── JobSink [data-testid="job-sink-{id}"]
│       ├── JobProgress [data-testid="job-progress-{id}"]
│       ├── JobDownloadBtn [data-testid="job-download-btn-{id}"]
│       └── JobRetryBtn [data-testid="job-retry-btn-{id}"]
│
├── ConnModal [data-testid="conn-modal"]
│   ├── ConnLabelInput [data-testid="conn-label-input"]
│   ├── ConnTypeSelect [data-testid="conn-type-select"]
│   ├── ConnRoleSelect [data-testid="conn-role-select"]
│   ├── ConnUrlInput [data-testid="conn-url-input"]
│   ├── ConnTestBtn [data-testid="conn-test-btn"]
│   └── ConnSubmitBtn [data-testid="conn-submit-btn"]
│
└── Toasts
    ├── ErrorToast [data-testid="error-toast"]
    └── SuccessToast [data-testid="success-toast"]
```

---

## 5. State Transitions (Schema Discovery)

| State | Schema Bar | Health Badge | Filter Row |
|---|---|---|---|
| Tab selected, no schema | Hidden | SYNCING… (gray, pulsing) | Shown, time badge hidden |
| Schema detecting | "Detecting schema…" (pulsing) | SYNCING… | Time badge hidden |
| Schema ready | Full section visible | HEALTHY STATUS (green) | Time badge shown with first timestamp field |
| Schema error | Error state + Retry button | ERROR (red) | Time badge hidden |
| No data in range | Yellow warning bar | HEALTHY STATUS | Unchanged |

---

## 6. Color / Design Tokens

Three custom tokens supplement Tailwind's default palette:

```css
:root {
  --color-primary:          #094cb2;   /* active tab underline, badges, Export button */
  --color-primary-fixed:    #d9e2ff;   /* time-field badge background tint */
  --color-tertiary-container: #bfab49; /* PROCESSED status badge background */
}
```

Typography:
- Labels / badges: `font-label` (Public Sans via Google Fonts, weight 400/600)
- Field paths / data values: system monospace (`font-mono`)
- Existing Inter font retained for body copy

---

## 7. data-testid Map

Complete map of all interactive UI elements and their Playwright-compatible `data-testid` values:

| Element | data-testid | Notes |
|---|---|---|
| Data Import nav tab | `nav-tab-import` | |
| Jobs nav tab | `nav-tab-jobs` | |
| Connection tab (per connection) | `conn-tab-{id}` | id = connection UUID |
| Add Connection tab | `add-connection-tab` | |
| Last synced label | `last-synced-label` | |
| Health badge | `health-badge` | text: HEALTHY STATUS / SYNCING… / ERROR |
| Refine Dataset card | `refine-dataset-card` | |
| Clear All button | `clear-all-btn` | visible only when filters active |
| Schema discovery section | `schema-discovery-section` | |
| Schema collapse/expand button | `schema-collapse-btn` | |
| Schema field chip | `schema-chip-{field}` | field name with dots replaced by dashes |
| Schema refresh button | `schema-refresh-btn` | |
| Columns picker button | `columns-picker-btn` | |
| Time field badge | `time-field-badge` | shows selected timestamp field name |
| Time range select | `time-range-select` | |
| Predicate filter input | `predicate-filter-input` | alias: `search-input` |
| Advanced filter button | `filter-advanced-btn` | |
| Advanced filter panel | `advanced-filter-panel` | |
| Filter rule row | `filter-rule-{n}` | n = 0-indexed |
| Data preview section | `data-preview-section` | |
| Visibility button | `visibility-btn` | |
| Limit badge | `limit-badge` | |
| Total rows display | `total-rows-display` | |
| Preview table | `preview-table` | |
| Preview row | `preview-row-{n}` | n = 0-indexed |
| Load more button | `load-more-btn` | |
| Data masking card | `data-masking-card` | |
| Masking toggle | `masking-toggle` | |
| Masking rules table | `masking-rules-table` | |
| Masking rule row | `masking-rule-{n}` | |
| Add masking rule button | `add-masking-rule-btn` | |
| PII auto-detection toggle | `pii-auto-detection-toggle` | |
| Sampling strategy card | `sampling-strategy-card` | |
| Sampling toggle | `sampling-toggle` | |
| Sampling method select | `sampling-method-select` | |
| Sampling method description | `sampling-method-desc` | |
| Sampling target column | `sampling-target-column` | |
| Sampling ratio | `sampling-ratio` | |
| Export destination section | `export-destination-section` | |
| Asset resolution toggle | `asset-resolution-toggle` | |
| Datasink select | `datasink-select` | |
| Export button | `export-btn` | |
| Webhook config card | `webhook-config-card` | |
| Webhook toggle | `webhook-toggle` | |
| Webhook URL input | `webhook-url-input` | |
| Test webhook button | `test-webhook-btn` | |
| Jobs view | `jobs-view` | |
| Jobs empty message | `jobs-empty-msg` | |
| Job row | `job-row-{id}` | |
| Job status badge | `job-status-{id}` | |
| Job source | `job-source-{id}` | |
| Job sink | `job-sink-{id}` | |
| Job progress | `job-progress-{id}` | |
| Job download button | `job-download-btn-{id}` | local-sink jobs only |
| Job retry button | `job-retry-btn-{id}` | failed jobs only |
| Error toast | `error-toast` | |
| Success toast | `success-toast` | |
| Connection modal | `conn-modal` | |
| Connection label input | `conn-label-input` | |
| Connection type select | `conn-type-select` | |
| Connection role select | `conn-role-select` | |
| Connection URL input | `conn-url-input` | |
| Connection test button | `conn-test-btn` | |
| Connection submit button | `conn-submit-btn` | |
