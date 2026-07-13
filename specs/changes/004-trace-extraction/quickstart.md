# Quickstart: Field Extraction Stage

**Phase 1 output** | **Date**: 2026-07-13

## Local verification (once implemented)

1. Start the stack as usual (`docker-compose up` / existing dev workflow — no new services required, this feature adds no dependencies).

2. In the browser UI, open a datasource whose records have a nested field holding structured content (e.g. an Amplitude-shaped source with `event_properties.trace` on some rows — one example among many valid extraction targets), configure an export destination, then:
   - Enable **Field Extraction** (`#field-extraction-toggle`)
   - Enter the field path, e.g. `event_properties.trace` (`#field-extraction-path-input`)
   - Click **Test** (`#test-field-extraction-btn`) to confirm the path resolves against sample records before running the full export
   - Run the export as normal

3. Confirm in the Jobs tab that `records_skipped` reflects any records lacking the configured field, and that the downloaded output contains only the extracted values (not the original envelopes).

## Direct API check

```bash
# Preview whether a field path resolves, without running a job
curl -X POST http://localhost:5010/api/v1/connections/<id>/test-field-extraction \
  -H "X-Group-ID: org1/user1" \
  -H "Content-Type: application/json" \
  -d '{"field_path": "event_properties.trace"}'

# Create a job with extraction enabled
curl -X POST http://localhost:5010/api/v1/export-jobs \
  -H "X-Group-ID: org1/user1" \
  -H "Content-Type: application/json" \
  -d '{
        "datasource_type": "connection",
        "datasource_ref": "<connection-id>",
        "datasink_name": "local-exports-jsonl",
        "destination_dataset": "amplitude_traces",
        "field_extraction": true,
        "field_extraction_path": "event_properties.trace"
      }'

# Misconfiguration check — expect 422
curl -X POST http://localhost:5010/api/v1/export-jobs \
  -H "X-Group-ID: org1/user1" \
  -H "Content-Type: application/json" \
  -d '{
        "datasource_type": "connection",
        "datasource_ref": "<connection-id>",
        "datasink_name": "local-exports-jsonl",
        "destination_dataset": "amplitude_traces",
        "field_extraction": true
      }'
```

## Test suite

```bash
pytest tests/unit/test_extraction.py
pytest tests/unit/test_export_worker.py -k extraction
pytest tests/unit/test_export_jobs_routes.py -k extraction
pytest tests/integration/test_datasinks_extraction.py
```
