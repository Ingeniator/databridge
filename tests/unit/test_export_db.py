"""Unit tests for export/db.py — mock pool, no real database."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from databridge.export.db import (
    cancel_export_job,
    count_active_jobs_for_org,
    get_export_job,
    insert_export_job,
    is_job_cancelled,
    list_export_jobs,
    update_export_job_status,
    update_export_progress,
    update_external_asset_dataset_id,
    update_external_dataset_id,
    update_records_total,
)
from databridge.export.models import ExportJobCreate, ExportJobStatus, FilterSnapshot

JOB_ID = uuid.uuid4()
ORG_ID = "org-1"
USER_ID = "user-1"
NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _row(**overrides):
    base = {
        "id": JOB_ID,
        "org_id": ORG_ID,
        "user_id": USER_ID,
        "datasource_type": "system",
        "datasource_ref": "my-source",
        "datasource_filter": json.dumps({"query": "", "start": None, "end": None, "time_field": None, "limit": 50}),
        "datasink_name": "my-sink",
        "destination_dataset": "out",
        "asset_resolution": False,
        "asset_url_fields": None,
        "asset_url_prefix": None,
        "asset_datasink_name": None,
        "asset_dataset": None,
        "status": "pending",
        "records_total": None,
        "records_processed": 0,
        "records_skipped": 0,
        "asset_errors": 0,
        "error_message": None,
        "created_at": NOW,
        "started_at": None,
        "completed_at": None,
        "masking_rules": None,
        "sampling_config": None,
        "webhook_url": None,
        "webhook_enabled": False,
        "webhook_payload_template": None,
        "external_dataset_id": None,
        "external_asset_dataset_id": None,
    }
    base.update(overrides)
    return base


def _pool(fetchrow=None, fetch=None, fetchval=None):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow)
    pool.fetch = AsyncMock(return_value=fetch or [])
    pool.fetchval = AsyncMock(return_value=fetchval if fetchval is not None else 0)
    pool.execute = AsyncMock()
    return pool


def _pool_with_acquire(conn_fetchval=0):
    """Pool whose .acquire() is an async context manager returning a mock connection."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=conn_fetchval)

    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=None)
    txn_cm.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=txn_cm)

    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool, mock_conn


# ── _parse_masking_rules / _parse_sampling_config (via _row_to_response) ─────

def test_row_to_response_with_json_string_filter():
    from databridge.export.db import _row_to_response
    row = _row(datasource_filter='{"query": "x", "limit": 10}')
    resp = _row_to_response(row)
    assert resp.datasource_filter.query == "x"
    assert resp.datasource_filter.limit == 10


def test_row_to_response_with_dict_filter():
    from databridge.export.db import _row_to_response
    row = _row(datasource_filter={"query": "y", "limit": 20})
    resp = _row_to_response(row)
    assert resp.datasource_filter.query == "y"


def test_row_to_response_with_json_url_fields():
    from databridge.export.db import _row_to_response
    row = _row(asset_url_fields='["image_url", "thumb_url"]')
    resp = _row_to_response(row)
    assert resp.asset_url_fields == ["image_url", "thumb_url"]


def test_row_to_response_with_masking_rules_json():
    from databridge.export.db import _row_to_response
    rules = json.dumps([{"field_path": "email", "action": "mask"}])
    row = _row(masking_rules=rules)
    resp = _row_to_response(row)
    assert len(resp.masking_rules) == 1
    assert resp.masking_rules[0].field_path == "email"


def test_row_to_response_with_masking_rules_list():
    from databridge.export.db import _row_to_response
    row = _row(masking_rules=[{"field_path": "ssn", "action": "hash"}])
    resp = _row_to_response(row)
    assert resp.masking_rules[0].field_path == "ssn"


def test_row_to_response_with_sampling_config_json():
    from databridge.export.db import _row_to_response
    cfg = json.dumps({"method": "random", "ratio_or_size": 0.5})
    row = _row(sampling_config=cfg)
    resp = _row_to_response(row)
    assert resp.sampling_config is not None
    assert resp.sampling_config.ratio_or_size == 0.5


def test_row_to_response_with_sampling_config_dict():
    from databridge.export.db import _row_to_response
    row = _row(sampling_config={"method": "systematic", "ratio_or_size": 100.0})
    resp = _row_to_response(row)
    assert resp.sampling_config.method.value == "systematic"


def test_row_to_response_minimal():
    from databridge.export.db import _row_to_response
    resp = _row_to_response(_row())
    assert resp.org_id == ORG_ID
    assert resp.status == ExportJobStatus.pending
    assert resp.masking_rules == []
    assert resp.sampling_config is None


# ── insert_export_job ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_insert_export_job_basic():
    pool = _pool(fetchrow=_row())
    data = ExportJobCreate(
        datasource_type="system",
        datasource_ref="src",
        datasink_name="my-sink",
        destination_dataset="out",
    )
    resp = await insert_export_job(pool, data, ORG_ID, USER_ID)
    assert resp.org_id == ORG_ID
    pool.fetchrow.assert_called_once()


@pytest.mark.asyncio
async def test_insert_export_job_asset_resolution_derives_dataset():
    returned = _row(
        asset_resolution=True,
        asset_dataset="out_assets",
        asset_datasink_name="my-sink",
    )
    pool = _pool(fetchrow=returned)
    data = ExportJobCreate(
        datasource_type="system",
        datasource_ref="src",
        datasink_name="my-sink",
        destination_dataset="out",
        asset_resolution=True,
    )
    resp = await insert_export_job(pool, data, ORG_ID, USER_ID)
    # Verify asset_dataset was set to "out_assets" template and passed to pool
    args = pool.fetchrow.call_args[0]
    assert "out_assets" in args  # derived from destination_dataset + "_assets"


@pytest.mark.asyncio
async def test_insert_export_job_explicit_asset_dataset():
    returned = _row(asset_resolution=True, asset_dataset="custom_ds")
    pool = _pool(fetchrow=returned)
    data = ExportJobCreate(
        datasource_type="system",
        datasource_ref="src",
        datasink_name="my-sink",
        destination_dataset="out",
        asset_resolution=True,
        asset_dataset="custom_ds",
    )
    resp = await insert_export_job(pool, data, ORG_ID, USER_ID)
    args = pool.fetchrow.call_args[0]
    assert "custom_ds" in args


@pytest.mark.asyncio
async def test_insert_export_job_no_asset_resolution_sets_none():
    pool = _pool(fetchrow=_row())
    data = ExportJobCreate(
        datasource_type="system",
        datasource_ref="src",
        datasink_name="my-sink",
        destination_dataset="out",
        asset_resolution=False,
    )
    await insert_export_job(pool, data, ORG_ID, USER_ID)
    args = pool.fetchrow.call_args[0]
    # asset_datasink_name and asset_dataset are None when resolution is off
    assert None in args


@pytest.mark.asyncio
async def test_insert_export_job_with_masking_and_sampling():
    from databridge.export.models import MaskingRule, SamplingConfig
    pool = _pool(fetchrow=_row(
        masking_rules=json.dumps([{"field_path": "email", "action": "mask"}]),
        sampling_config=json.dumps({"method": "random", "ratio_or_size": 0.1}),
    ))
    data = ExportJobCreate(
        datasource_type="system",
        datasource_ref="src",
        datasink_name="my-sink",
        destination_dataset="out",
        masking_rules=[MaskingRule(field_path="email", action="mask")],
        sampling_config=SamplingConfig(ratio_or_size=0.1),
    )
    resp = await insert_export_job(pool, data, ORG_ID, USER_ID)
    assert len(resp.masking_rules) == 1
    assert resp.sampling_config is not None


# ── get_export_job ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_export_job_not_found():
    pool = _pool(fetchrow=None)
    result = await get_export_job(pool, JOB_ID, ORG_ID, USER_ID, "user")
    assert result is None


@pytest.mark.asyncio
async def test_get_export_job_super_admin_sees_any():
    pool = _pool(fetchrow=_row(org_id="other-org", user_id="other-user"))
    result = await get_export_job(pool, JOB_ID, ORG_ID, USER_ID, "super_admin")
    assert result is not None


@pytest.mark.asyncio
async def test_get_export_job_org_admin_sees_same_org():
    pool = _pool(fetchrow=_row(org_id=ORG_ID, user_id="other-user"))
    result = await get_export_job(pool, JOB_ID, ORG_ID, "admin-user", "org_admin")
    assert result is not None


@pytest.mark.asyncio
async def test_get_export_job_org_admin_cannot_see_other_org():
    pool = _pool(fetchrow=_row(org_id="other-org", user_id="other-user"))
    result = await get_export_job(pool, JOB_ID, ORG_ID, USER_ID, "org_admin")
    assert result is None


@pytest.mark.asyncio
async def test_get_export_job_user_sees_own():
    pool = _pool(fetchrow=_row(org_id=ORG_ID, user_id=USER_ID))
    result = await get_export_job(pool, JOB_ID, ORG_ID, USER_ID, "user")
    assert result is not None


@pytest.mark.asyncio
async def test_get_export_job_user_cannot_see_others():
    pool = _pool(fetchrow=_row(org_id=ORG_ID, user_id="someone-else"))
    result = await get_export_job(pool, JOB_ID, ORG_ID, USER_ID, "user")
    assert result is None


# ── list_export_jobs ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_export_jobs_super_admin_no_org_filter():
    pool = _pool(fetch=[_row()], fetchval=1)
    results, total = await list_export_jobs(pool, ORG_ID, USER_ID, "super_admin")
    assert total == 1
    assert len(results) == 1
    # super_admin: no org_id param passed to query
    fetchval_args = pool.fetchval.call_args[0]
    assert ORG_ID not in fetchval_args


@pytest.mark.asyncio
async def test_list_export_jobs_user_filters_by_org():
    pool = _pool(fetch=[_row()], fetchval=1)
    results, total = await list_export_jobs(pool, ORG_ID, USER_ID, "user")
    assert total == 1
    fetchval_args = pool.fetchval.call_args[0]
    assert ORG_ID in fetchval_args


@pytest.mark.asyncio
async def test_list_export_jobs_status_filter():
    pool = _pool(fetch=[], fetchval=0)
    results, total = await list_export_jobs(pool, ORG_ID, USER_ID, "user", status_filter="running")
    assert total == 0
    fetchval_args = pool.fetchval.call_args[0]
    assert "running" in fetchval_args


@pytest.mark.asyncio
async def test_list_export_jobs_pagination():
    pool = _pool(fetch=[_row()], fetchval=10)
    results, total = await list_export_jobs(pool, ORG_ID, USER_ID, "super_admin", page=2, page_size=5)
    assert total == 10
    fetch_args = pool.fetch.call_args[0]
    # offset = (2-1)*5 = 5, page_size = 5
    assert 5 in fetch_args


@pytest.mark.asyncio
async def test_list_export_jobs_empty():
    pool = _pool(fetch=[], fetchval=0)
    results, total = await list_export_jobs(pool, ORG_ID, USER_ID, "user")
    assert results == []
    assert total == 0


# ── update_export_job_status ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_status_running():
    pool = _pool()
    await update_export_job_status(pool, JOB_ID, ExportJobStatus.running)
    pool.execute.assert_called_once()
    sql = pool.execute.call_args[0][0]
    assert "started_at" in sql


@pytest.mark.asyncio
async def test_update_status_completed():
    pool = _pool()
    await update_export_job_status(pool, JOB_ID, ExportJobStatus.completed)
    pool.execute.assert_called_once()
    sql = pool.execute.call_args[0][0]
    assert "completed_at" in sql


@pytest.mark.asyncio
async def test_update_status_failed_with_message():
    pool = _pool()
    await update_export_job_status(pool, JOB_ID, ExportJobStatus.failed, error_message="boom")
    pool.execute.assert_called_once()
    args = pool.execute.call_args[0]
    assert "boom" in args


@pytest.mark.asyncio
async def test_update_status_other():
    pool = _pool()
    await update_export_job_status(pool, JOB_ID, ExportJobStatus.cancelled)
    pool.execute.assert_called_once()
    sql = pool.execute.call_args[0][0]
    assert "completed_at" not in sql
    assert "started_at" not in sql


# ── update_export_progress ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_export_progress():
    pool = _pool()
    await update_export_progress(pool, JOB_ID, records_processed=10, records_skipped=2, asset_errors=1)
    pool.execute.assert_called_once()
    args = pool.execute.call_args[0]
    assert 10 in args
    assert 2 in args
    assert 1 in args


# ── update_records_total ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_records_total():
    pool = _pool()
    await update_records_total(pool, JOB_ID, 999)
    pool.execute.assert_called_once()
    args = pool.execute.call_args[0]
    assert 999 in args


# ── update_external_dataset_id ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_external_dataset_id():
    pool = _pool()
    await update_external_dataset_id(pool, JOB_ID, "ext-ds-123")
    pool.execute.assert_called_once()
    args = pool.execute.call_args[0]
    assert "ext-ds-123" in args


# ── update_external_asset_dataset_id ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_external_asset_dataset_id():
    pool = _pool()
    await update_external_asset_dataset_id(pool, JOB_ID, "ext-asset-ds-456")
    pool.execute.assert_called_once()
    args = pool.execute.call_args[0]
    assert "ext-asset-ds-456" in args


# ── cancel_export_job ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_job_not_found():
    pool = _pool(fetchrow=None)
    result = await cancel_export_job(pool, JOB_ID, ORG_ID, USER_ID, "user")
    assert result is False


@pytest.mark.asyncio
async def test_cancel_job_wrong_user():
    pool = _pool(fetchrow={"org_id": ORG_ID, "user_id": "other", "status": "pending"})
    result = await cancel_export_job(pool, JOB_ID, ORG_ID, USER_ID, "user")
    assert result is False


@pytest.mark.asyncio
async def test_cancel_job_wrong_status():
    pool = _pool(fetchrow={"org_id": ORG_ID, "user_id": USER_ID, "status": "completed"})
    result = await cancel_export_job(pool, JOB_ID, ORG_ID, USER_ID, "user")
    assert result is False


@pytest.mark.asyncio
async def test_cancel_job_success():
    pool = _pool(fetchrow={"org_id": ORG_ID, "user_id": USER_ID, "status": "pending"})
    pool.execute.return_value = "UPDATE 1"
    result = await cancel_export_job(pool, JOB_ID, ORG_ID, USER_ID, "user")
    assert result is True


@pytest.mark.asyncio
async def test_cancel_job_already_updated_elsewhere():
    pool = _pool(fetchrow={"org_id": ORG_ID, "user_id": USER_ID, "status": "running"})
    pool.execute.return_value = "UPDATE 0"
    result = await cancel_export_job(pool, JOB_ID, ORG_ID, USER_ID, "user")
    assert result is False


@pytest.mark.asyncio
async def test_cancel_job_org_admin_same_org():
    pool = _pool(fetchrow={"org_id": ORG_ID, "user_id": "anyone", "status": "pending"})
    pool.execute.return_value = "UPDATE 1"
    result = await cancel_export_job(pool, JOB_ID, ORG_ID, "admin", "org_admin")
    assert result is True


@pytest.mark.asyncio
async def test_cancel_job_org_admin_other_org_blocked():
    pool = _pool(fetchrow={"org_id": "other-org", "user_id": "anyone", "status": "pending"})
    result = await cancel_export_job(pool, JOB_ID, ORG_ID, "admin", "org_admin")
    assert result is False


@pytest.mark.asyncio
async def test_cancel_job_super_admin_any_org():
    pool = _pool(fetchrow={"org_id": "other-org", "user_id": "anyone", "status": "running"})
    pool.execute.return_value = "UPDATE 1"
    result = await cancel_export_job(pool, JOB_ID, ORG_ID, USER_ID, "super_admin")
    assert result is True


# ── is_job_cancelled ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_is_job_cancelled_true():
    pool = _pool(fetchval="cancelled")
    # fetchval is set to "cancelled" string
    pool.fetchval = AsyncMock(return_value="cancelled")
    result = await is_job_cancelled(pool, JOB_ID)
    assert result is True


@pytest.mark.asyncio
async def test_is_job_cancelled_false():
    pool = _pool()
    pool.fetchval = AsyncMock(return_value="running")
    result = await is_job_cancelled(pool, JOB_ID)
    assert result is False


# ── count_active_jobs_for_org ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_count_active_jobs_returns_count():
    pool, conn = _pool_with_acquire(conn_fetchval=3)
    result = await count_active_jobs_for_org(pool, ORG_ID)
    assert result == 3
    assert conn.execute.call_count == 3  # advisory lock + expire running + expire pending


@pytest.mark.asyncio
async def test_count_active_jobs_zero():
    pool, conn = _pool_with_acquire(conn_fetchval=0)
    result = await count_active_jobs_for_org(pool, ORG_ID)
    assert result == 0
