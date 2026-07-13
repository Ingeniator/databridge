"""Unit tests for databridge.export.worker.run_export_job."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from databridge.config import (
    DatasinkConfig,
    ExportSettings,
    ServerConfig,
    Settings,
    SystemSourceConfig,
)
from databridge.export.models import ExportJobStatus
from databridge.export.worker import run_export_job

_JOB_ID = str(uuid.uuid4())
_ORG_ID = "org-1"
_CONN_ID = str(uuid.uuid4())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(**kw) -> Settings:
    defaults = dict(
        server=ServerConfig(),
        database_url="postgresql://x",
        encryption_key="Ks_7gv0quQuNMvwLHNhToPPgrQw7Z3Zjm2r-4mTJqF4=",
        datasources=(),
        datasinks=(
            DatasinkConfig(name="mock-sink", type="local-jsonl", path="/tmp"),
        ),
        export=ExportSettings(batch_size=10),
    )
    defaults.update(kw)
    return Settings(**defaults)


def _job_row(**kw) -> dict:
    row = {
        "id": uuid.UUID(_JOB_ID),
        "org_id": _ORG_ID,
        "user_id": "user-1",
        "status": "pending",
        "datasink_name": "mock-sink",
        "datasource_type": "system",
        "datasource_ref": "src",
        "datasource_filter": None,
        "masking_rules": None,
        "sampling_config": None,
        "asset_resolution": False,
        "asset_url_fields": None,
        "asset_url_prefix": None,
        "asset_datasink_name": None,
        "asset_dataset": None,
        "destination_dataset": "out",
        "webhook_url": None,
        "webhook_enabled": False,
        "webhook_payload_template": None,
        "field_extraction": False,
        "field_extraction_path": "",
    }
    row.update(kw)
    return row


def _make_pool(job_row=None, conn_row=None):
    pool = MagicMock()

    async def fetchrow(query, *args):
        if "export_jobs" in query:
            return job_row
        if "connections" in query:
            return conn_row
        return None

    pool.fetchrow = fetchrow
    pool.execute = AsyncMock()
    return pool


def _make_sink():
    sink = MagicMock()
    sink.ping = AsyncMock()
    sink.create_dataset = AsyncMock()
    sink.post_file = AsyncMock()
    sink.finalise = AsyncMock()
    sink.records_skipped = 0
    return sink


def _make_adapter(records=None):
    adapter = MagicMock()
    adapter.count = AsyncMock(return_value=len(records or []))
    adapter.fetch_page = AsyncMock(return_value=records or [])
    return adapter


def _make_paged_adapter(all_records):
    """Adapter that slices the record list per (offset, limit) — mirrors a real datasource."""
    adapter = MagicMock()
    adapter.count = AsyncMock(return_value=len(all_records))

    async def _fetch(query, start, end, limit, offset):
        return all_records[offset:offset + limit]

    adapter.fetch_page = _fetch
    return adapter


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_PATCH_UPDATE_STATUS = "databridge.export.worker.update_export_job_status"
_PATCH_UPDATE_PROGRESS = "databridge.export.worker.update_export_progress"
_PATCH_UPDATE_TOTAL = "databridge.export.worker.update_records_total"
_PATCH_IS_CANCELLED = "databridge.export.worker.is_job_cancelled"
_PATCH_GET_SINK = "databridge.sinks.get_sink"
_PATCH_GET_ADAPTER = "databridge.adapters.get_adapter"
_PATCH_DECRYPT = "databridge.crypto.decrypt_credentials"

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_job_not_found_returns_early():
    pool = _make_pool(job_row=None)
    ctx = {"pool": pool, "settings": _settings()}
    with patch(_PATCH_UPDATE_STATUS) as mock_status:
        await run_export_job(ctx, _JOB_ID)
    mock_status.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_job_returns_early():
    pool = _make_pool(job_row=_job_row(status="cancelled"))
    ctx = {"pool": pool, "settings": _settings()}
    with patch(_PATCH_UPDATE_STATUS) as mock_status:
        await run_export_job(ctx, _JOB_ID)
    mock_status.assert_not_called()


@pytest.mark.asyncio
async def test_datasink_not_found_marks_failed():
    pool = _make_pool(job_row=_job_row(datasink_name="nonexistent"))
    ctx = {"pool": pool, "settings": _settings()}
    with patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock) as mock_status:
        await run_export_job(ctx, _JOB_ID)
    mock_status.assert_called_once()
    _, kwargs = mock_status.call_args
    assert mock_status.call_args[0][2] == ExportJobStatus.failed


@pytest.mark.asyncio
async def test_system_source_success():
    src = SystemSourceConfig(
        name="src", type="clickhouse",
        url="http://ch:8123", database="d", table="t", user="u", password="",
    )
    settings = _settings(datasources=(src,))
    pool = _make_pool(job_row=_job_row())
    adapter = _make_adapter(records=[{"a": 1}, {"a": 2}])
    sink = _make_sink()

    with (
        patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_PROGRESS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_TOTAL, new_callable=AsyncMock),
        patch(_PATCH_IS_CANCELLED, new_callable=AsyncMock, return_value=False),
        patch(_PATCH_GET_ADAPTER, return_value=adapter) as mock_adapter,
        patch(_PATCH_GET_SINK, return_value=sink),
    ):
        ctx = {"pool": pool, "settings": settings}
        await run_export_job(ctx, _JOB_ID)

    assert sink.post_file.call_count == 2
    sink.finalise.assert_called_once()


@pytest.mark.asyncio
async def test_connection_source_calls_decrypt_credentials():
    """Regression: worker must call decrypt_credentials, not the non-existent decrypt."""
    creds = {"host": "db.example.com", "password": "secret"}
    conn_row = {"id": uuid.UUID(_CONN_ID), "type": "postgres", "credentials_enc": b"enc"}
    pool = _make_pool(
        job_row=_job_row(datasource_type="connection", datasource_ref=_CONN_ID),
        conn_row=conn_row,
    )
    adapter = _make_adapter(records=[])
    sink = _make_sink()

    with (
        patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_PROGRESS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_TOTAL, new_callable=AsyncMock),
        patch(_PATCH_IS_CANCELLED, new_callable=AsyncMock, return_value=False),
        patch(_PATCH_DECRYPT, return_value=creds) as mock_decrypt,
        patch(_PATCH_GET_ADAPTER, return_value=adapter),
        patch(_PATCH_GET_SINK, return_value=sink),
    ):
        ctx = {"pool": pool, "settings": _settings()}
        await run_export_job(ctx, _JOB_ID)

    mock_decrypt.assert_called_once_with(b"enc")


@pytest.mark.asyncio
async def test_connection_not_found_marks_failed():
    pool = _make_pool(
        job_row=_job_row(datasource_type="connection", datasource_ref=_CONN_ID),
        conn_row=None,
    )

    with (
        patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock) as mock_status,
        patch(_PATCH_IS_CANCELLED, new_callable=AsyncMock, return_value=False),
    ):
        ctx = {"pool": pool, "settings": _settings()}
        await run_export_job(ctx, _JOB_ID)

    statuses = [call.args[2] for call in mock_status.call_args_list]
    assert ExportJobStatus.failed in statuses


@pytest.mark.asyncio
async def test_cancelled_mid_run_stops_loop():
    src = SystemSourceConfig(
        name="src", type="clickhouse",
        url="http://ch:8123", database="d", table="t", user="u", password="",
    )
    settings = _settings(datasources=(src,))
    pool = _make_pool(job_row=_job_row())
    adapter = _make_adapter(records=[{"x": i} for i in range(20)])
    sink = _make_sink()

    call_count = 0

    async def is_cancelled(pool, job_id):
        nonlocal call_count
        call_count += 1
        return call_count >= 1  # cancel after first batch

    with (
        patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_PROGRESS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_TOTAL, new_callable=AsyncMock),
        patch(_PATCH_IS_CANCELLED, side_effect=is_cancelled),
        patch(_PATCH_GET_ADAPTER, return_value=adapter),
        patch(_PATCH_GET_SINK, return_value=sink),
    ):
        ctx = {"pool": pool, "settings": settings}
        await run_export_job(ctx, _JOB_ID)

    # Cancelled after first batch: fetch_page must not be called a second time
    assert adapter.fetch_page.call_count == 1


# ---------------------------------------------------------------------------
# Sampling cap — end-to-end through the worker
# ---------------------------------------------------------------------------

def _src():
    return SystemSourceConfig(
        name="src", type="clickhouse",
        url="http://ch:8123", database="d", table="t", user="u", password="",
    )


@pytest.mark.asyncio
async def test_sampling_cap_exports_exactly_cap_records():
    """Worker posts exactly max_items records when supply is sufficient (single batch)."""
    all_records = [{"id": i} for i in range(50)]
    pool = _make_pool(
        job_row=_job_row(
            sampling_config={"method": "random", "ratio_or_size": 1.0, "max_items": 20}
        )
    )
    adapter = _make_paged_adapter(all_records)
    sink = _make_sink()

    with (
        patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_PROGRESS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_TOTAL, new_callable=AsyncMock),
        patch(_PATCH_IS_CANCELLED, new_callable=AsyncMock, return_value=False),
        patch(_PATCH_GET_ADAPTER, return_value=adapter),
        patch(_PATCH_GET_SINK, return_value=sink),
    ):
        ctx = {"pool": pool, "settings": _settings(datasources=(_src(),))}
        await run_export_job(ctx, _JOB_ID)

    assert sink.post_file.call_count == 20


@pytest.mark.asyncio
async def test_sampling_cap_enforced_across_multiple_batches():
    """Cap stops the loop mid-batch when records span several adapter pages."""
    all_records = [{"id": i} for i in range(20)]
    pool = _make_pool(
        job_row=_job_row(
            sampling_config={"method": "random", "ratio_or_size": 1.0, "max_items": 8}
        )
    )
    adapter = _make_paged_adapter(all_records)
    sink = _make_sink()

    with (
        patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_PROGRESS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_TOTAL, new_callable=AsyncMock),
        patch(_PATCH_IS_CANCELLED, new_callable=AsyncMock, return_value=False),
        patch(_PATCH_GET_ADAPTER, return_value=adapter),
        patch(_PATCH_GET_SINK, return_value=sink),
    ):
        ctx = {
            "pool": pool,
            "settings": _settings(datasources=(_src(),), export=ExportSettings(batch_size=5)),
        }
        await run_export_job(ctx, _JOB_ID)

    assert sink.post_file.call_count == 8


@pytest.mark.asyncio
async def test_no_cap_exports_all_records():
    """Without max_items every record from the adapter is posted to the sink."""
    all_records = [{"id": i} for i in range(15)]
    pool = _make_pool(job_row=_job_row())
    adapter = _make_paged_adapter(all_records)
    sink = _make_sink()

    with (
        patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_PROGRESS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_TOTAL, new_callable=AsyncMock),
        patch(_PATCH_IS_CANCELLED, new_callable=AsyncMock, return_value=False),
        patch(_PATCH_GET_ADAPTER, return_value=adapter),
        patch(_PATCH_GET_SINK, return_value=sink),
    ):
        ctx = {"pool": pool, "settings": _settings(datasources=(_src(),))}
        await run_export_job(ctx, _JOB_ID)

    assert sink.post_file.call_count == 15


# ---------------------------------------------------------------------------
# T010 [US1] — field extraction stage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_field_extraction_replaces_record_with_extracted_value():
    """When the field path resolves, the sink receives the extracted value, not the envelope."""
    all_records = [
        {"event_properties": {"trace": {"span_id": "abc", "duration_ms": 42}}, "other": "envelope-only"},
    ]
    pool = _make_pool(job_row=_job_row(field_extraction=True, field_extraction_path="event_properties.trace"))
    adapter = _make_paged_adapter(all_records)
    sink = _make_sink()

    with (
        patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_PROGRESS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_TOTAL, new_callable=AsyncMock),
        patch(_PATCH_IS_CANCELLED, new_callable=AsyncMock, return_value=False),
        patch(_PATCH_GET_ADAPTER, return_value=adapter),
        patch(_PATCH_GET_SINK, return_value=sink),
    ):
        ctx = {"pool": pool, "settings": _settings(datasources=(_src(),))}
        await run_export_job(ctx, _JOB_ID)

    sink.post_file.assert_called_once()
    posted_record = sink.post_file.call_args.args[1]
    assert posted_record == {"span_id": "abc", "duration_ms": 42}


@pytest.mark.asyncio
async def test_field_extraction_skips_record_when_path_unresolved():
    """Missing/unusable content at the field path drops the record and counts it as skipped."""
    all_records = [
        {"event_properties": {"trace": {"span_id": "abc"}}},  # resolves
        {"event_properties": {}},  # missing field
        {"event_properties": {"trace": "not json"}},  # unusable plain string
    ]
    pool = _make_pool(job_row=_job_row(field_extraction=True, field_extraction_path="event_properties.trace"))
    adapter = _make_paged_adapter(all_records)
    sink = _make_sink()

    with (
        patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_PROGRESS, new_callable=AsyncMock) as mock_progress,
        patch(_PATCH_UPDATE_TOTAL, new_callable=AsyncMock),
        patch(_PATCH_IS_CANCELLED, new_callable=AsyncMock, return_value=False),
        patch(_PATCH_GET_ADAPTER, return_value=adapter),
        patch(_PATCH_GET_SINK, return_value=sink),
    ):
        ctx = {"pool": pool, "settings": _settings(datasources=(_src(),))}
        await run_export_job(ctx, _JOB_ID)

    assert sink.post_file.call_count == 1
    # last progress update call: (pool, job_id, records_processed, records_skipped, asset_errors)
    last_call = mock_progress.call_args_list[-1]
    assert last_call.args[2] == 1  # records_processed
    assert last_call.args[3] == 2  # records_skipped


@pytest.mark.asyncio
async def test_field_extraction_disabled_records_pass_through_unchanged():
    """field_extraction=False (default) leaves existing behavior untouched."""
    all_records = [{"a": 1}, {"a": 2}]
    pool = _make_pool(job_row=_job_row())  # field_extraction defaults to False
    adapter = _make_paged_adapter(all_records)
    sink = _make_sink()

    with (
        patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_PROGRESS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_TOTAL, new_callable=AsyncMock),
        patch(_PATCH_IS_CANCELLED, new_callable=AsyncMock, return_value=False),
        patch(_PATCH_GET_ADAPTER, return_value=adapter),
        patch(_PATCH_GET_SINK, return_value=sink),
    ):
        ctx = {"pool": pool, "settings": _settings(datasources=(_src(),))}
        await run_export_job(ctx, _JOB_ID)

    assert sink.post_file.call_count == 2
    posted = [call.args[1] for call in sink.post_file.call_args_list]
    assert posted == all_records


# ---------------------------------------------------------------------------
# T017 [US2] — field extraction runs before masking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_field_extraction_then_masking_masks_extracted_field():
    """A masking rule targeting a field inside the extracted content is applied."""
    all_records = [
        {"event_properties": {"trace": {"span_id": "abc", "user_email": "u@x.com"}}},
    ]
    pool = _make_pool(job_row=_job_row(
        field_extraction=True,
        field_extraction_path="event_properties.trace",
        masking_rules=[{"field_path": "user_email", "action": "mask"}],
    ))
    adapter = _make_paged_adapter(all_records)
    sink = _make_sink()

    with (
        patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_PROGRESS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_TOTAL, new_callable=AsyncMock),
        patch(_PATCH_IS_CANCELLED, new_callable=AsyncMock, return_value=False),
        patch(_PATCH_GET_ADAPTER, return_value=adapter),
        patch(_PATCH_GET_SINK, return_value=sink),
    ):
        ctx = {"pool": pool, "settings": _settings(datasources=(_src(),))}
        await run_export_job(ctx, _JOB_ID)

    posted_record = sink.post_file.call_args.args[1]
    assert posted_record["user_email"] == "***"
    assert posted_record["span_id"] == "abc"


@pytest.mark.asyncio
async def test_field_extraction_metrics_increment():
    """T019 [US3] — EXPORT_FIELD_EXTRACTION_SUCCESS/_FAILED counters reflect outcomes."""
    from databridge.export_metrics import EXPORT_FIELD_EXTRACTION_FAILED, EXPORT_FIELD_EXTRACTION_SUCCESS

    success_before = EXPORT_FIELD_EXTRACTION_SUCCESS._value.get()
    failed_before = EXPORT_FIELD_EXTRACTION_FAILED._value.get()

    all_records = [
        {"event_properties": {"trace": {"span_id": "abc"}}},  # resolves -> success
        {"event_properties": {}},  # missing -> failed
    ]
    pool = _make_pool(job_row=_job_row(field_extraction=True, field_extraction_path="event_properties.trace"))
    adapter = _make_paged_adapter(all_records)
    sink = _make_sink()

    with (
        patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_PROGRESS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_TOTAL, new_callable=AsyncMock),
        patch(_PATCH_IS_CANCELLED, new_callable=AsyncMock, return_value=False),
        patch(_PATCH_GET_ADAPTER, return_value=adapter),
        patch(_PATCH_GET_SINK, return_value=sink),
    ):
        ctx = {"pool": pool, "settings": _settings(datasources=(_src(),))}
        await run_export_job(ctx, _JOB_ID)

    assert EXPORT_FIELD_EXTRACTION_SUCCESS._value.get() == success_before + 1
    assert EXPORT_FIELD_EXTRACTION_FAILED._value.get() == failed_before + 1


@pytest.mark.asyncio
async def test_field_extraction_then_masking_envelope_only_rule_is_noop():
    """A masking rule targeting an envelope-only field has no effect and does not error."""
    all_records = [
        {"event_properties": {"trace": {"span_id": "abc"}}, "envelope_secret": "shh"},
    ]
    pool = _make_pool(job_row=_job_row(
        field_extraction=True,
        field_extraction_path="event_properties.trace",
        masking_rules=[{"field_path": "envelope_secret", "action": "mask"}],
    ))
    adapter = _make_paged_adapter(all_records)
    sink = _make_sink()

    with (
        patch(_PATCH_UPDATE_STATUS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_PROGRESS, new_callable=AsyncMock),
        patch(_PATCH_UPDATE_TOTAL, new_callable=AsyncMock),
        patch(_PATCH_IS_CANCELLED, new_callable=AsyncMock, return_value=False),
        patch(_PATCH_GET_ADAPTER, return_value=adapter),
        patch(_PATCH_GET_SINK, return_value=sink),
    ):
        ctx = {"pool": pool, "settings": _settings(datasources=(_src(),))}
        await run_export_job(ctx, _JOB_ID)

    posted_record = sink.post_file.call_args.args[1]
    assert posted_record == {"span_id": "abc"}
