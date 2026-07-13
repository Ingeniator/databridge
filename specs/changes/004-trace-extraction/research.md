# Research: Field Extraction Stage

**Phase 0 output** | **Date**: 2026-07-13

No unresolved `NEEDS CLARIFICATION` markers carried in from the spec — both open questions (FR-007's definition of "usable content," and the feature's canonical name) were resolved with the user during `/speckit-specify`/`/speckit-clarify`. The decisions below are implementation-level choices made during planning, not open unknowns.

---

### Decision 0: Feature name — `field_extraction`, not `trace_extraction`

**Decision**: The feature, its config fields, module, endpoint, metrics, and docs are all named around the generic mechanism (`field_extraction`), not the motivating example (`trace`).

**Rationale**: The original request was framed around Amplitude's `event_properties.trace`, but the mechanism itself — resolve a dotted field path, transparently through JSON-encoded string containers, and promote whatever structured value is found there — has nothing trace-specific about it. Any nested JSON value inside any enveloped field is a valid target. Locking the name to "trace" would have baked a single use case into permanent API/DB/metric surface. Confirmed with the user during clarification: `field_extraction` was chosen over `record_extraction` and `nested_extraction` for consistency with this codebase's existing naming for opt-in per-record stages (`masking`, `sampling`, `asset_resolution` — all short, generic nouns).

**Alternatives considered**:
- `trace_extraction` (original framing): rejected — scopes the name to one example, misleading once other envelope shapes (not just Amplitude traces) are extracted with it.
- `record_extraction`: considered, but "record" is ambiguous with the export job's own unit of work (an export job already processes "records"); `field_extraction` is more precise about what's being read.
- `nested_extraction`: considered, but doesn't name what's being extracted (a field), just that it's nested.

---

### Decision 1: Stage placement — after sampling, before masking

**Decision**: The worker's per-record loop becomes `sampling → field extraction → masking → asset resolution → sink`, inserting the new stage immediately after sampling.

**Rationale**: Masking exists to protect sensitive content before it reaches a destination. If extraction ran *after* masking, masking rules would be applied to the original envelope — which is about to be discarded — while the promoted extracted content would reach the sink completely unmasked. Running extraction first means masking rules configured against the extracted payload's own field names (not the envelope's) do their job. This was the explicit reasoning behind User Story 2 in the spec.

**Alternatives considered**:
- *After masking, before assets*: rejected — defeats the purpose of combining the two features; sensitive fields inside the extracted payload would ship unmasked.
- *After asset resolution*: rejected — asset resolution reads `asset_url_fields`, which are field names in the original envelope shape; running it after extraction would silently stop working unless assets are reconfigured for the extracted shape. Keeping extraction first and letting asset resolution run against whatever record survives is simpler and matches the "each stage operates on the previous stage's output" pattern already used for sampling → masking → assets.

---

### Decision 2: Definition of "usable extracted content"

**Decision**: A resolved value is usable only if it is a native `dict`/`list`, or a `str` that `json.loads` successfully parses into a `dict`/`list`. Anything else — missing field, empty string, plain non-JSON string, or a JSON-encoded bare scalar (e.g. `"123"`, `"true"`) — is treated as unusable and causes a skip.

**Rationale**: Confirmed with the user (spec FR-007, Option B) over the alternative of treating any non-empty string as valid content. This guarantees the output dataset is structurally consistent (every exported record is an object/array), which matters because downstream consumers of an extracted-content dataset expect structured records, not a mix of objects and bare strings.

**Alternatives considered**:
- *Any non-empty value usable, raw strings passed through as-is*: this was the initial framing from the feature request ("get value line as real trace") but was explicitly rejected by the user during spec clarification in favor of the stricter rule.

---

### Decision 3: Shared read-only path-resolution helper

**Decision**: Factor the "descend a dotted path through a dict, transparently `json.loads`-ing any string container encountered along the way" logic out of `masking._apply_at_path` into a shared helper usable in both mutate mode (masking) and read-only mode (extraction), rather than reimplementing the traversal in `export/extraction.py`.

**Rationale**: `masking.py`'s existing docstring already documents why this transparent-JSON-string descent exists — schema inference surfaces dotted paths reaching through stringified JSON, so masking has to reach through it too. Field extraction has the identical problem (an enveloped field, e.g. Amplitude's `event_properties`, is frequently itself a JSON-encoded string in the raw row). Constitution §IV requires logic duplicated across ≥2 modules to be extracted; this is exactly that case.

**Alternatives considered**:
- *Duplicate a simplified version in `extraction.py`*: rejected — two copies of subtle JSON-descent logic drift apart over time (e.g. one gets a bugfix the other doesn't).

---

### Decision 4: Single field path, not a list

**Decision**: `field_extraction_path: str` (singular), paired with `field_extraction: bool`, added directly to `ExportJob`/`ExportJobCreate`/`ExportJobResponse` — not a new entry type inside `masking_rules` and not a `list[str]` like `asset_url_fields`.

**Rationale**: Established in the pre-planning discussion with the user: extraction has 0-or-1 cardinality per job (a job either designates one field to extract or it doesn't), architecturally closer to `asset_resolution`'s single-toggle shape than to `masking_rules`'s open-ended list. This was also the user's explicit design request ("take only one key").

---

### Decision 5: Reject invalid configuration at creation time

**Decision**: Add a Pydantic `model_validator` on `ExportJobCreate` that raises when `field_extraction=True` and `field_extraction_path` is empty/whitespace, surfacing as a `422` from `POST /api/v1/export-jobs`.

**Rationale**: Spec FR-003 explicitly requires rejecting this configuration rather than accepting it and silently skipping every record at run time. This is cheap to enforce at the model layer, consistent with how other required-when-enabled fields could be validated, and avoids a class of "job completed with 0 records processed and no error" support questions.

**Alternatives considered**:
- *No validation — treat empty path as no-op, same as asset resolution's current behavior when `asset_url_fields` is empty*: rejected — the spec explicitly calls for rejection (FR-003), and unlike asset resolution (where an empty field list is a legitimate "nothing to resolve" state), an enabled-but-unconfigured extraction stage would deterministically skip 100% of records, which is a configuration error, not a legitimate empty case.

---

### Decision 6: Add a preview/test endpoint

**Decision**: Add `POST /api/v1/connections/{id}/test-field-extraction`, taking a candidate field path and returning per-sample-record resolution outcomes (resolved value or failure reason), mirroring the existing `POST /connections/{id}/test-asset-resolution`.

**Rationale**: SC-001 requires configuring the feature "without needing to write custom code or a post-processing step." Without a preview, the only way to validate a field path is to run a full export job and observe the skip count afterward — an expensive, slow feedback loop for what is fundamentally a typo-prone text field. The codebase already has this exact pattern (`test-asset-resolution`) for the same underlying problem (validate a per-record field-path guess against live sample data before committing to a full job).

**Alternatives considered**:
- *No preview endpoint; rely on the job's `records_skipped` count after the fact*: rejected as inconsistent with the precedent already set by asset resolution's test endpoint, and a worse experience for the exact failure mode this feature is most likely to hit (wrong field name).

---

### Decision 7: Pipeline order change is a formal contract update

**Decision**: Treat the record-loop order change as a declared, documented change to `specs/current/export-pipeline.md`, plus an explicit acceptance test asserting extraction runs before masking — not an incidental side effect left implicit in `worker.py`.

**Rationale**: Constitution §VIII designates the worker's step order as part of the behavioral contract; any change requires an updated spec and updated acceptance tests. Since the new stage is opt-in and defaults to off, no currently-configured job's behavior changes — but the order itself is still new, contractual surface once a job turns it on, so it gets the same documentation treatment as the original order did.
