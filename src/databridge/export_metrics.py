from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

EXPORT_JOBS_CREATED = Counter(
    "export_jobs_created_total",
    "Export jobs created",
    ["org_id", "sink_type"],
)
EXPORT_JOBS_COMPLETED = Counter(
    "export_jobs_completed_total",
    "Export jobs completed successfully",
    ["org_id", "sink_type"],
)
EXPORT_JOBS_FAILED = Counter(
    "export_jobs_failed_total",
    "Export jobs failed",
    ["org_id", "sink_type"],
)
EXPORT_ACTIVE_JOBS = Gauge(
    "export_active_jobs",
    "Currently active (running + pending) export jobs",
    ["org_id"],
)
EXPORT_RECORDS_PER_SECOND = Gauge(
    "export_records_per_second",
    "Records exported per second (updated per batch)",
    ["sink_type"],
)
EXPORT_ASSET_RESOLUTION_SUCCESS = Counter(
    "export_asset_resolution_success_total",
    "Assets successfully fetched and stored",
)
EXPORT_ASSET_RESOLUTION_FAILED = Counter(
    "export_asset_resolution_failed_total",
    "Asset fetches that failed (record skipped)",
)
EXPORT_FIELD_EXTRACTION_SUCCESS = Counter(
    "export_field_extraction_success_total",
    "Records successfully reduced to their extracted field value",
)
EXPORT_FIELD_EXTRACTION_FAILED = Counter(
    "export_field_extraction_failed_total",
    "Records skipped because the configured field extraction path did not resolve to usable content",
)
EXPORT_ORG_CONCURRENT_JOBS = Gauge(
    "export_org_concurrent_jobs",
    "Concurrent export jobs per org (running + pending)",
    ["org_id"],
)
MASKING_RULES_APPLIED = Counter(
    "masking_rules_applied_total",
    "Records that had masking rules applied",
    ["org_id"],
)
SAMPLING_RECORDS_DROPPED = Counter(
    "sampling_records_dropped_total",
    "Records dropped by sampling strategy",
    ["org_id"],
)
WEBHOOK_DELIVERY = Counter(
    "webhook_delivery_total",
    "Webhook delivery attempts",
    ["org_id", "status"],
)
PII_FIELDS_REQUEST_DURATION = Histogram(
    "pii_fields_request_duration_seconds",
    "Latency of GET /pii-fields requests",
    ["connection_type"],
)
PREVIEW_REQUEST_DURATION = Histogram(
    "preview_request_duration_seconds",
    "Latency of POST /preview requests",
    ["connection_type"],
)
