from __future__ import annotations

from prometheus_client import Counter, Gauge

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
EXPORT_ORG_CONCURRENT_JOBS = Gauge(
    "export_org_concurrent_jobs",
    "Concurrent export jobs per org (running + pending)",
    ["org_id"],
)
