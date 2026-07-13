# Feature Specification: Field Extraction Stage

**Feature Branch**: `003-trace-extraction`

**Created**: 2026-07-12

**Status**: Draft

**Input**: User description: "Add a trace-extraction stage to the export pipeline. Export jobs need an opt-in option to extract a single nested field from each record (e.g. event_properties.<key> in Amplitude-shaped event JSON) and replace the entire record with that field's value before it reaches the sink, instead of exporting the raw event envelope. This is a per-job toggle (trace_extraction: bool, trace_field_path: str), analogous to the existing asset_resolution option, not a masking rule -- it runs as its own pipeline stage before masking, replacing the record rather than mutating a field in place. If the field is missing or its value can't be parsed as the trace payload, the record should be skipped and counted in records_skipped, consistent with how asset resolution failures are handled today."

## Clarifications

### Session 2026-07-13

- Q: What should this feature be called, given it's not specific to traces — any nested JSON value inside an enveloped field can be extracted, of which a trace payload (e.g. Amplitude's `event_properties.trace`) is just one case? → A: `field_extraction` — generic name, no use-case baked in, consistent with this codebase's naming for other opt-in per-record stages (`masking`, `sampling`, `asset_resolution`).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Export the nested value directly instead of the raw envelope (Priority: P1)

Someone configuring an export job for an envelope-shaped data source (e.g. Amplitude-style events, where the meaningful payload is buried inside a nested field such as `event_properties`) wants to designate a single field whose value is the real content of interest — a trace payload, or any other structured JSON value — and have the export contain that content directly, not the surrounding envelope.

**Why this priority**: This is the entire point of the feature. Without it, the only way to get clean nested data out of a source is to export the full raw envelope and post-process it outside the tool, which is exactly what this feature exists to avoid.

**Independent Test**: Configure an export job against a source where records contain a nested field holding structured content, enable field extraction with that field's path, run the job, and confirm the destination dataset contains the extracted values as standalone records rather than full envelopes.

**Acceptance Scenarios**:

1. **Given** an export job with field extraction enabled and a field path pointing at a field nested inside a record, **When** the job processes a record that has usable content at that path, **Then** the record written to the destination is that field's value alone, not the original envelope.
2. **Given** a record where the configured field path does not resolve to any value, **When** the job processes that record, **Then** the record is skipped, counted among the job's skipped records, and processing continues with the next record.
3. **Given** field extraction is not enabled for a job, **When** the job runs, **Then** records are exported exactly as before (unchanged default behavior).

---

### User Story 2 - Masking still protects extracted content (Priority: P2)

Someone who relies on masking rules to redact sensitive fields before export wants those rules to keep working when field extraction is also enabled — masking should apply to the promoted extracted content, not to the discarded envelope.

**Why this priority**: Without this, enabling field extraction could silently bypass an org's existing compliance/privacy safeguards, which is a correctness and trust issue, not just a convenience gap.

**Independent Test**: Configure a job with both field extraction and a masking rule targeting a field that exists within the extracted content, run it, and confirm the field is masked in the output.

**Acceptance Scenarios**:

1. **Given** a job with field extraction enabled and a masking rule targeting a field that exists in the extracted content, **When** the job runs, **Then** that field is masked in the exported record.
2. **Given** a job with field extraction enabled and a masking rule targeting a field that only existed in the original envelope (not in the extracted content), **When** the job runs, **Then** the rule has no effect (there is nothing left to mask), and this does not cause an error.

---

### User Story 3 - Visibility into extraction outcomes (Priority: P3)

Someone monitoring an export job wants to see how many records were successfully extracted versus skipped due to a missing or unusable field, using the same progress and skip-count reporting the export pipeline already provides.

**Why this priority**: Useful for diagnosing a misconfigured field path (e.g. every record skipped signals the path is wrong) but not required for the feature to deliver its core value.

**Independent Test**: Run a job against a source where some records have the configured field and others don't, and confirm the reported skipped count matches the number of records lacking usable content at that path.

**Acceptance Scenarios**:

1. **Given** a job where some records lack the configured field, **When** the job completes, **Then** the job's skipped-record count includes those records.

---

### Edge Cases

- What happens when field extraction is enabled but no field path is configured? System should reject the job configuration up front rather than skipping every record at run time.
- What happens when the configured field path partially resolves (e.g. an intermediate segment of the path exists but is not a container that can be descended into)? Treated the same as "field not found" — record is skipped and counted.
- What happens when the field's container along the path is itself stored as an encoded string rather than a native nested structure? Extraction should still be able to reach through it, consistent with how field-path resolution already behaves elsewhere in the export pipeline.
- What happens when the extracted value is present but empty, or is a plain non-JSON string? Both are treated as unusable content — record is skipped and counted (only JSON-parseable content counts as usable, per FR-007).
- What happens when both field extraction and asset resolution are enabled on the same job? Asset resolution runs against whatever record field extraction produced — no special-casing required, since the two settings are independently configurable and the pipeline simply runs one stage after the other.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST allow an export job to opt into field extraction; the default for existing and newly created jobs is off (unchanged current behavior).
- **FR-002**: When field extraction is enabled, the job MUST require exactly one configured field path identifying where the content of interest lives within each source record.
- **FR-003**: System MUST reject job configuration where field extraction is enabled but no field path is provided.
- **FR-004**: For each source record, when field extraction is enabled, system MUST attempt to resolve the configured field path, including through any nested containers stored as encoded strings along the way, consistent with existing field-path resolution behavior in the export pipeline.
- **FR-005**: When the field path resolves to usable content, system MUST replace the entire record with that content before it proceeds further through the pipeline — the original envelope MUST NOT be exported.
- **FR-006**: When the field path does not resolve, or resolves to content that is not usable (see FR-007), system MUST skip that record, increment the job's skipped-record count, and continue processing without failing the job.
- **FR-007**: A value is considered "usable extracted content" only when it is structured, JSON-parseable content. A resolved value that is a plain, non-JSON string MUST be treated the same as a missing field — the record is skipped and counted, not exported as a raw string.
- **FR-008**: Field extraction MUST run before masking rules are applied, so that masking rules configured for the job act on the extracted content rather than the discarded envelope.
- **FR-009**: Field extraction and masking MUST be independently configurable — a job may enable either, both, or neither.
- **FR-010**: The skipped-record count produced by field extraction MUST be reported through the same job-level skipped/progress accounting already used elsewhere in the export pipeline (e.g. for asset resolution failures), not a separate counter.

### Key Entities

- **Export Job Configuration**: gains two new attributes — whether field extraction is enabled, and the single field path designating where the content of interest lives within each source record.
- **Extracted Content**: the value located at the configured field path within a source record (a trace payload is one example; any structured JSON value is valid); once extracted, this value becomes the entire record that continues through the rest of the export pipeline (masking, further processing, sink delivery).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can enable field extraction on an export job and specify the source field with a single configuration change, without writing custom code or a post-processing step.
- **SC-002**: For a source where every record contains usable content at the configured field path, the number of records in the exported dataset equals the number of source records (no unintended drops).
- **SC-003**: For a source where some records lack usable content at the configured field path, 100% of those records are excluded from the output and reflected in the job's skipped-record count, with zero job failures caused solely by such records.
- **SC-004**: Masking rules that correctly redacted content before this feature continue to correctly redact the same content once it is relocated into the extracted content.

## Assumptions

- Extraction is one-to-one: one source record yields at most one exported record (the extracted value itself), never multiple output records from a single source record's field.
- Only one field path can be configured per job — this is a single designated field, not a list.
- Field path resolution follows the same dotted-path convention, including transparent traversal through encoded-string containers, already established elsewhere in the export pipeline (e.g. for masking rule targets), so behavior is consistent across features.
- This is an opt-in, per-job setting with no effect on jobs that don't enable it.
- Asset resolution, when also enabled, operates on the record as it exists after field extraction has run.
- "Usable extracted content" means JSON-parseable content specifically; plain-string values at the configured field path are treated as a skip, not exported as-is.
- A trace payload (e.g. Amplitude's `event_properties.trace`) is one motivating example of extracted content, not the definition of the feature's scope — any nested JSON value reachable by a dotted field path is a valid extraction target.
