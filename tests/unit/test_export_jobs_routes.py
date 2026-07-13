"""Unit tests for routes/export_jobs.py — no real DB, ARQ, or network."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.testclient import TestClient

from databridge.auth import AuthContext
from databridge.config import DatasinkConfig, ExportSettings
from databridge.export.models import (
    ExportJobResponse,
    ExportJobStatus,
    FilterSnapshot,
)
from databridge.routes.export_jobs import router

FAKE_AUTH = AuthContext(public_key="org/user", org_id="org-1", user_id="user-1")
JOB_ID = uuid.uuid4()
NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _job(**overrides) -> ExportJobResponse:
    defaults = dict(
        id=JOB_ID,
        org_id="org-1",
        user_id="user-1",
        datasource_type="system",
        datasource_ref="src",
        datasource_filter=FilterSnapshot(),
        datasink_name="my-sink",
        destination_dataset="out",
        asset_resolution=False,
        asset_url_fields=[],
        asset_url_prefix="",
        asset_datasink_name=None,
        asset_dataset=None,
        status=ExportJobStatus.pending,
        records_total=None,
        records_processed=0,
        records_skipped=0,
        asset_errors=0,
        error_message=None,
        created_at=NOW,
        started_at=None,
        completed_at=None,
    )
    defaults.update(overrides)
    return ExportJobResponse(**defaults)


def _mock_settings(datasinks=(), max_concurrent=5, public_url="", webhook_prefixes=()):
    s = MagicMock()
    s.datasinks = list(datasinks)
    s.export.max_concurrent_jobs_per_org = max_concurrent
    s.server.public_url = public_url
    s.export.webhook_allowed_url_prefixes = tuple(webhook_prefixes)
    return s


def _sink(name="my-sink", type="local-jsonl", url="", path="/tmp"):
    return DatasinkConfig(name=name, type=type, url=url, path=path)


def _mock_arq(ping_error=None, enqueue_error=None):
    arq = AsyncMock()
    if ping_error:
        arq.ping = AsyncMock(side_effect=ping_error)
    else:
        arq.ping = AsyncMock()
    if enqueue_error:
        arq.enqueue_job = AsyncMock(side_effect=enqueue_error)
    else:
        arq.enqueue_job = AsyncMock()
    return arq


# ── App / client fixtures ─────────────────────────────────────────────────────

@pytest.fixture()
def mock_pool():
    return AsyncMock()


@pytest.fixture()
def app(mock_pool):
    from databridge.auth import get_auth
    from databridge.db.pool import get_pool

    _app = FastAPI()
    _app.include_router(router)
    _app.dependency_overrides[get_auth] = lambda: FAKE_AUTH
    _app.dependency_overrides[get_pool] = lambda: mock_pool
    return _app


@pytest.fixture()
def client(app):
    return TestClient(app, raise_server_exceptions=True)


# ── _with_download_urls ───────────────────────────────────────────────────────

def test_download_url_local_jsonl_no_base():
    from databridge.routes.export_jobs import _with_download_urls
    job = _job()
    settings = _mock_settings(datasinks=[_sink(type="local-jsonl")])
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        result = _with_download_urls(job)
    assert result.download_url == f"/api/v1/export-jobs/{JOB_ID}/download"


def test_download_url_local_zip_with_base_url():
    from databridge.routes.export_jobs import _with_download_urls
    job = _job()
    settings = _mock_settings(
        datasinks=[_sink(type="local-zip")],
        public_url="https://databridge.example.com",
    )
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        result = _with_download_urls(job)
    assert result.download_url == f"https://databridge.example.com/api/v1/export-jobs/{JOB_ID}/download"


def test_download_url_local_sink_with_asset_resolution():
    from databridge.routes.export_jobs import _with_download_urls
    job = _job(
        asset_resolution=True,
        asset_datasink_name="asset-sink",
    )
    settings = _mock_settings(datasinks=[
        _sink(name="my-sink", type="local-jsonl"),
        _sink(name="asset-sink", type="local-zip"),
    ])
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        result = _with_download_urls(job)
    assert result.download_url is not None
    assert result.assets_download_url is not None
    assert "assets=true" in result.assets_download_url


def test_download_url_dataset_mock_with_external_id():
    from databridge.routes.export_jobs import _with_download_urls
    job = _job(external_dataset_id="ds-abc")
    settings = _mock_settings(datasinks=[_sink(type="dataset-mock", url="http://mock.example.com")])
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        result = _with_download_urls(job)
    assert result.download_url == "http://mock.example.com/_mock/datasets/ds-abc"


def test_download_url_dataset_mock_without_external_id():
    from databridge.routes.export_jobs import _with_download_urls
    job = _job()
    settings = _mock_settings(datasinks=[_sink(type="dataset-mock", url="http://mock.example.com")])
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        result = _with_download_urls(job)
    assert result.download_url == "http://mock.example.com/_mock/datasets"


def test_download_url_dataset_mock_assets():
    from databridge.routes.export_jobs import _with_download_urls
    job = _job(
        asset_resolution=True,
        asset_datasink_name="asset-sink",
        external_asset_dataset_id="asset-ds-xyz",
    )
    settings = _mock_settings(datasinks=[
        _sink(name="my-sink", type="dataset-mock", url="http://mock.example.com"),
        _sink(name="asset-sink", type="dataset-mock", url="http://asset.example.com"),
    ])
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        result = _with_download_urls(job)
    assert result.assets_download_url == "http://asset.example.com/_mock/datasets/asset-ds-xyz"


def test_download_url_annotator_mock_with_external_id():
    from databridge.routes.export_jobs import _with_download_urls
    job = _job(external_dataset_id="task-99")
    settings = _mock_settings(datasinks=[_sink(type="annotator-mock", url="http://annotator.example.com")])
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        result = _with_download_urls(job)
    assert result.download_url == "http://annotator.example.com/api/v0/statistics/task/task-99"


def test_download_url_annotator_mock_without_external_id():
    from databridge.routes.export_jobs import _with_download_urls
    job = _job()
    settings = _mock_settings(datasinks=[_sink(type="annotator-mock", url="http://annotator.example.com")])
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        result = _with_download_urls(job)
    assert result.download_url == "http://annotator.example.com/api/v0/tasks"


def test_download_url_other_sink_type():
    from databridge.routes.export_jobs import _with_download_urls
    job = _job()
    settings = _mock_settings(datasinks=[_sink(type="s3", url="s3://my-bucket/path")])
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        result = _with_download_urls(job)
    assert result.download_url == "s3://my-bucket/path"


def test_download_url_no_matching_sink():
    from databridge.routes.export_jobs import _with_download_urls
    job = _job(datasink_name="nonexistent")
    settings = _mock_settings(datasinks=[_sink(name="other-sink", type="local-jsonl")])
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        result = _with_download_urls(job)
    assert result.download_url == ""


# ── _validate_webhook_url ─────────────────────────────────────────────────────

def test_validate_webhook_url_https_ok():
    from databridge.routes.export_jobs import _validate_webhook_url
    _validate_webhook_url("https://hooks.example.com/notify", ())  # must not raise


def test_validate_webhook_url_http_ok():
    from databridge.routes.export_jobs import _validate_webhook_url
    _validate_webhook_url("http://hooks.example.com/notify", ())


def test_validate_webhook_url_bad_scheme():
    from databridge.routes.export_jobs import _validate_webhook_url
    with pytest.raises(HTTPException) as exc_info:
        _validate_webhook_url("ftp://hooks.example.com/notify", ())
    assert exc_info.value.status_code == 400


def test_validate_webhook_url_prefix_allowed():
    from databridge.routes.export_jobs import _validate_webhook_url
    _validate_webhook_url("https://hooks.example.com/notify", ("https://hooks.example.com",))


def test_validate_webhook_url_prefix_not_allowed():
    from databridge.routes.export_jobs import _validate_webhook_url
    with pytest.raises(HTTPException) as exc_info:
        _validate_webhook_url("https://evil.example.com/notify", ("https://hooks.example.com",))
    assert exc_info.value.status_code == 400


# ── _local_file_response ──────────────────────────────────────────────────────

def test_local_file_response_found_with_job_id(tmp_path):
    from databridge.routes.export_jobs import _local_file_response
    sink_cfg = _sink(type="local-jsonl", path=str(tmp_path))
    job_id = uuid.uuid4()
    filepath = tmp_path / f"out_{job_id}.jsonl"
    filepath.write_text('{"x":1}')
    resp = _local_file_response(sink_cfg, "out", job_id)
    assert str(filepath) == resp.path


def test_local_file_response_found_without_job_id(tmp_path):
    from databridge.routes.export_jobs import _local_file_response
    sink_cfg = _sink(type="local-zip", path=str(tmp_path))
    job_id = uuid.uuid4()
    filepath = tmp_path / "out.zip"
    filepath.write_bytes(b"PK")
    resp = _local_file_response(sink_cfg, "out", job_id)
    assert str(filepath) == resp.path


def test_local_file_response_not_found(tmp_path):
    from databridge.routes.export_jobs import _local_file_response
    sink_cfg = _sink(type="local-jsonl", path=str(tmp_path))
    with pytest.raises(HTTPException) as exc_info:
        _local_file_response(sink_cfg, "out", uuid.uuid4())
    assert exc_info.value.status_code == 404


# ── POST /api/v1/export-jobs ──────────────────────────────────────────────────

_BODY = {
    "datasource_type": "system",
    "datasource_ref": "src",
    "datasink_name": "my-sink",
    "destination_dataset": "out",
}


def test_create_export_job_datasink_not_configured(client):
    settings = _mock_settings(datasinks=[])
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        r = client.post("/api/v1/export-jobs", json=_BODY)
    assert r.status_code == 400
    assert "datasink" in r.json()["detail"]


def test_create_export_job_concurrent_limit(client):
    settings = _mock_settings(datasinks=[_sink()], max_concurrent=2)
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.count_active_jobs_for_org", new_callable=AsyncMock, return_value=2),
    ):
        r = client.post("/api/v1/export-jobs", json=_BODY)
    assert r.status_code == 429


def test_create_export_job_arq_pool_unavailable(client):
    settings = _mock_settings(datasinks=[_sink()])
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.count_active_jobs_for_org", new_callable=AsyncMock, return_value=0),
        patch("databridge.routes.export_jobs._get_arq_pool", return_value=None),
    ):
        r = client.post("/api/v1/export-jobs", json=_BODY)
    assert r.status_code == 503
    assert "queue unavailable" in r.json()["detail"]


def test_create_export_job_arq_ping_fails(client):
    settings = _mock_settings(datasinks=[_sink()])
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.count_active_jobs_for_org", new_callable=AsyncMock, return_value=0),
        patch("databridge.routes.export_jobs._get_arq_pool", return_value=_mock_arq(ping_error=ConnectionError("down"))),
    ):
        r = client.post("/api/v1/export-jobs", json=_BODY)
    assert r.status_code == 503
    assert "unreachable" in r.json()["detail"]


def test_create_export_job_enqueue_fails(client):
    settings = _mock_settings(datasinks=[_sink()])
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.count_active_jobs_for_org", new_callable=AsyncMock, return_value=0),
        patch("databridge.routes.export_jobs._get_arq_pool", return_value=_mock_arq(enqueue_error=RuntimeError("redis full"))),
        patch("databridge.routes.export_jobs.insert_export_job", new_callable=AsyncMock, return_value=_job()),
    ):
        r = client.post("/api/v1/export-jobs", json=_BODY)
    assert r.status_code == 503
    assert "enqueue" in r.json()["detail"]


def test_create_export_job_success(client):
    settings = _mock_settings(datasinks=[_sink()])
    job = _job(status=ExportJobStatus.pending)
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.count_active_jobs_for_org", new_callable=AsyncMock, return_value=0),
        patch("databridge.routes.export_jobs._get_arq_pool", return_value=_mock_arq()),
        patch("databridge.routes.export_jobs.insert_export_job", new_callable=AsyncMock, return_value=job),
    ):
        r = client.post("/api/v1/export-jobs", json=_BODY)
    assert r.status_code == 201
    assert r.json()["destination_dataset"] == "out"


# ── T011 [US1] field_extraction validation ────────────────────────────────────

def test_create_export_job_field_extraction_without_path_returns_422(client):
    settings = _mock_settings(datasinks=[_sink()])
    body = {**_BODY, "field_extraction": True}
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        r = client.post("/api/v1/export-jobs", json=body)
    assert r.status_code == 422
    assert "field_extraction_path" in r.text


def test_create_export_job_field_extraction_with_empty_path_returns_422(client):
    settings = _mock_settings(datasinks=[_sink()])
    body = {**_BODY, "field_extraction": True, "field_extraction_path": "   "}
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        r = client.post("/api/v1/export-jobs", json=body)
    assert r.status_code == 422


def test_create_export_job_field_extraction_with_path_succeeds(client):
    settings = _mock_settings(datasinks=[_sink()])
    job = _job(status=ExportJobStatus.pending, field_extraction=True, field_extraction_path="event_properties.trace")
    body = {**_BODY, "field_extraction": True, "field_extraction_path": "event_properties.trace"}
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.count_active_jobs_for_org", new_callable=AsyncMock, return_value=0),
        patch("databridge.routes.export_jobs._get_arq_pool", return_value=_mock_arq()),
        patch("databridge.routes.export_jobs.insert_export_job", new_callable=AsyncMock, return_value=job),
    ):
        r = client.post("/api/v1/export-jobs", json=body)
    assert r.status_code == 201
    assert r.json()["field_extraction"] is True
    assert r.json()["field_extraction_path"] == "event_properties.trace"


def test_create_export_job_field_extraction_default_false_no_path_required(client):
    settings = _mock_settings(datasinks=[_sink()])
    job = _job(status=ExportJobStatus.pending)
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.count_active_jobs_for_org", new_callable=AsyncMock, return_value=0),
        patch("databridge.routes.export_jobs._get_arq_pool", return_value=_mock_arq()),
        patch("databridge.routes.export_jobs.insert_export_job", new_callable=AsyncMock, return_value=job),
    ):
        r = client.post("/api/v1/export-jobs", json=_BODY)
    assert r.status_code == 201
    assert r.json()["field_extraction"] is False


# ── GET /api/v1/export-jobs ───────────────────────────────────────────────────

def test_list_export_jobs_empty(client):
    settings = _mock_settings()
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.list_export_jobs", new_callable=AsyncMock, return_value=([], 0)),
    ):
        r = client.get("/api/v1/export-jobs")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_export_jobs_returns_items(client):
    settings = _mock_settings(datasinks=[_sink()])
    jobs = [_job()]
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.list_export_jobs", new_callable=AsyncMock, return_value=(jobs, 1)),
    ):
        r = client.get("/api/v1/export-jobs?page=1&page_size=10")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1
    assert r.json()["page"] == 1
    assert r.json()["page_size"] == 10


def test_list_export_jobs_with_status_filter(client):
    settings = _mock_settings()
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.list_export_jobs", new_callable=AsyncMock, return_value=([], 0)) as mock_list,
    ):
        r = client.get("/api/v1/export-jobs?status=running")
    assert r.status_code == 200
    mock_list.assert_called_once()
    assert mock_list.call_args.kwargs["status_filter"] == "running"


# ── GET /api/v1/export-jobs/{job_id} ─────────────────────────────────────────

def test_get_export_job_not_found(client):
    settings = _mock_settings()
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=None),
    ):
        r = client.get(f"/api/v1/export-jobs/{JOB_ID}")
    assert r.status_code == 404


def test_get_export_job_found(client):
    settings = _mock_settings(datasinks=[_sink()])
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=_job()),
    ):
        r = client.get(f"/api/v1/export-jobs/{JOB_ID}")
    assert r.status_code == 200
    assert r.json()["id"] == str(JOB_ID)


# ── POST /api/v1/export-jobs/{job_id}/retry ───────────────────────────────────

def test_retry_original_not_found(client):
    settings = _mock_settings()
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=None),
    ):
        r = client.post(f"/api/v1/export-jobs/{JOB_ID}/retry")
    assert r.status_code == 404


def test_retry_non_failed_job(client):
    settings = _mock_settings()
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=_job(status=ExportJobStatus.completed)),
    ):
        r = client.post(f"/api/v1/export-jobs/{JOB_ID}/retry")
    assert r.status_code == 400
    assert "failed" in r.json()["detail"]


def test_retry_at_concurrent_limit(client):
    settings = _mock_settings(max_concurrent=1)
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=_job(status=ExportJobStatus.failed)),
        patch("databridge.routes.export_jobs.count_active_jobs_for_org", new_callable=AsyncMock, return_value=1),
    ):
        r = client.post(f"/api/v1/export-jobs/{JOB_ID}/retry")
    assert r.status_code == 429


def test_retry_arq_unavailable(client):
    settings = _mock_settings()
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=_job(status=ExportJobStatus.failed)),
        patch("databridge.routes.export_jobs.count_active_jobs_for_org", new_callable=AsyncMock, return_value=0),
        patch("databridge.routes.export_jobs._get_arq_pool", return_value=None),
    ):
        r = client.post(f"/api/v1/export-jobs/{JOB_ID}/retry")
    assert r.status_code == 503


def test_retry_success(client):
    settings = _mock_settings(datasinks=[_sink()])
    new_id = uuid.uuid4()
    new_job = _job(id=new_id, status=ExportJobStatus.pending)
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=_job(status=ExportJobStatus.failed)),
        patch("databridge.routes.export_jobs.count_active_jobs_for_org", new_callable=AsyncMock, return_value=0),
        patch("databridge.routes.export_jobs._get_arq_pool", return_value=_mock_arq()),
        patch("databridge.routes.export_jobs.insert_export_job", new_callable=AsyncMock, return_value=new_job),
    ):
        r = client.post(f"/api/v1/export-jobs/{JOB_ID}/retry")
    assert r.status_code == 201
    assert r.json()["id"] == str(new_id)


def test_retry_enqueue_fails(client):
    settings = _mock_settings(datasinks=[_sink()])
    new_job = _job(id=uuid.uuid4(), status=ExportJobStatus.pending)
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=_job(status=ExportJobStatus.failed)),
        patch("databridge.routes.export_jobs.count_active_jobs_for_org", new_callable=AsyncMock, return_value=0),
        patch("databridge.routes.export_jobs._get_arq_pool", return_value=_mock_arq(enqueue_error=RuntimeError("redis down"))),
        patch("databridge.routes.export_jobs.insert_export_job", new_callable=AsyncMock, return_value=new_job),
    ):
        r = client.post(f"/api/v1/export-jobs/{JOB_ID}/retry")
    assert r.status_code == 503


# ── POST /api/v1/export-jobs/{job_id}/cancel ─────────────────────────────────

def test_cancel_job_success(client):
    with patch("databridge.routes.export_jobs.cancel_export_job", new_callable=AsyncMock, return_value=True):
        r = client.post(f"/api/v1/export-jobs/{JOB_ID}/cancel")
    assert r.status_code == 204


def test_cancel_job_not_found(client):
    with (
        patch("databridge.routes.export_jobs.cancel_export_job", new_callable=AsyncMock, return_value=False),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=None),
    ):
        r = client.post(f"/api/v1/export-jobs/{JOB_ID}/cancel")
    assert r.status_code == 404


def test_cancel_job_wrong_status(client):
    with (
        patch("databridge.routes.export_jobs.cancel_export_job", new_callable=AsyncMock, return_value=False),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock,
              return_value=_job(status=ExportJobStatus.completed)),
    ):
        r = client.post(f"/api/v1/export-jobs/{JOB_ID}/cancel")
    assert r.status_code == 409
    assert "completed" in r.json()["detail"]


# ── GET /api/v1/export-jobs/{job_id}/download ────────────────────────────────

def test_download_job_not_found(client):
    settings = _mock_settings()
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=None),
    ):
        r = client.get(f"/api/v1/export-jobs/{JOB_ID}/download")
    assert r.status_code == 404


def test_download_job_not_completed(client):
    settings = _mock_settings()
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock,
              return_value=_job(status=ExportJobStatus.running)),
    ):
        r = client.get(f"/api/v1/export-jobs/{JOB_ID}/download")
    assert r.status_code == 409
    assert "running" in r.json()["detail"]


def test_download_non_local_sink_returns_400(client):
    settings = _mock_settings(datasinks=[_sink(type="dataset-mock", url="http://mock.example.com")])
    job = _job(status=ExportJobStatus.completed)
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=job),
    ):
        r = client.get(f"/api/v1/export-jobs/{JOB_ID}/download")
    assert r.status_code == 400
    assert "local" in r.json()["detail"]


def test_download_assets_no_resolution_configured(client):
    settings = _mock_settings(datasinks=[_sink(type="local-jsonl")])
    job = _job(status=ExportJobStatus.completed, asset_resolution=False)
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=job),
    ):
        r = client.get(f"/api/v1/export-jobs/{JOB_ID}/download?assets=true")
    assert r.status_code == 400
    assert "asset resolution" in r.json()["detail"]


def test_download_assets_non_local_sink_returns_400(client):
    settings = _mock_settings(datasinks=[
        _sink(name="my-sink", type="local-jsonl"),
        _sink(name="asset-sink", type="dataset-mock", url="http://mock.example.com"),
    ])
    job = _job(
        status=ExportJobStatus.completed,
        asset_resolution=True,
        asset_datasink_name="asset-sink",
        asset_dataset="out_assets",
    )
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=job),
    ):
        r = client.get(f"/api/v1/export-jobs/{JOB_ID}/download?assets=true")
    assert r.status_code == 400


def test_download_success(client, tmp_path):
    sink = _sink(type="local-jsonl", path=str(tmp_path))
    settings = _mock_settings(datasinks=[sink])
    job = _job(status=ExportJobStatus.completed)
    outfile = tmp_path / f"out_{JOB_ID}.jsonl"
    outfile.write_text('{"x":1}')
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=job),
    ):
        r = client.get(f"/api/v1/export-jobs/{JOB_ID}/download")
    assert r.status_code == 200


def test_download_assets_success(client, tmp_path):
    asset_sink = _sink(name="asset-sink", type="local-jsonl", path=str(tmp_path))
    settings = _mock_settings(datasinks=[_sink(), asset_sink])
    job = _job(
        status=ExportJobStatus.completed,
        asset_resolution=True,
        asset_datasink_name="asset-sink",
        asset_dataset="out_assets",
    )
    outfile = tmp_path / f"out_assets_{JOB_ID}.jsonl"
    outfile.write_text('{"asset":1}')
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        patch("databridge.routes.export_jobs.get_export_job", new_callable=AsyncMock, return_value=job),
    ):
        r = client.get(f"/api/v1/export-jobs/{JOB_ID}/download?assets=true")
    assert r.status_code == 200


# ── POST /api/v1/export-jobs/test-webhook ────────────────────────────────────

def test_webhook_bad_scheme(client):
    settings = _mock_settings()
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        r = client.post("/api/v1/export-jobs/test-webhook", json={"url": "ftp://bad.example.com"})
    assert r.status_code == 400


def test_webhook_prefix_not_allowed(client):
    settings = _mock_settings(webhook_prefixes=("https://allowed.example.com",))
    with patch("databridge.routes.export_jobs.get_settings", return_value=settings):
        r = client.post("/api/v1/export-jobs/test-webhook", json={"url": "https://evil.example.com/hook"})
    assert r.status_code == 400


def test_webhook_delivery_success(client):
    settings = _mock_settings()
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        respx.mock,
    ):
        respx.post("https://hooks.example.com/notify").mock(return_value=httpx.Response(200))
        r = client.post("/api/v1/export-jobs/test-webhook", json={"url": "https://hooks.example.com/notify"})
    assert r.status_code == 204


def test_webhook_delivery_failure(client):
    settings = _mock_settings()
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        respx.mock,
    ):
        respx.post("https://hooks.example.com/notify").mock(return_value=httpx.Response(500))
        r = client.post("/api/v1/export-jobs/test-webhook", json={"url": "https://hooks.example.com/notify"})
    assert r.status_code == 502


def test_webhook_with_custom_template(client):
    settings = _mock_settings()
    with (
        patch("databridge.routes.export_jobs.get_settings", return_value=settings),
        respx.mock,
    ):
        respx.post("https://hooks.example.com/notify").mock(return_value=httpx.Response(200))
        r = client.post("/api/v1/export-jobs/test-webhook", json={
            "url": "https://hooks.example.com/notify",
            "template": '{"event": "test", "job": "{{job_id}}"}',
        })
    assert r.status_code == 204


# ── set_arq_pool ──────────────────────────────────────────────────────────────

def test_set_arq_pool():
    from databridge.routes.export_jobs import set_arq_pool, _get_arq_pool
    import databridge.routes.export_jobs as mod
    original = mod._arq_pool_ref
    try:
        mock = object()
        set_arq_pool(mock)
        assert _get_arq_pool() is mock
    finally:
        mod._arq_pool_ref = original
