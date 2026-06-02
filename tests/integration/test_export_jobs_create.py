"""Integration test stubs — POST/GET export jobs."""
import json
import textwrap
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

SINK_NAME = "test-sink"


@pytest.fixture
def config_with_sinks(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(textwrap.dedent(f"""
        server:
          port: 5010
          debug: true
          silence_probes: false
          hide_auth_inputs: false
        database_url: "postgresql://postgres:postgres@localhost:5432/databridge_test"
        encryption_key: "{key}"
        datasources: []
        datasinks:
          - name: "{SINK_NAME}"
            type: local-jsonl
            path: "/tmp/test_exports"
        export:
          max_concurrent_jobs_per_org: 5
    """))
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(cfg))
    yield cfg


class _ExportPool:
    """Fake pool for export_jobs tests."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}

    def _make_job(self, org_id, user_id, datasource_type, datasource_ref,
                  datasource_filter, datasink_name, destination_dataset,
                  asset_resolution, asset_url_fields, asset_url_prefix,
                  asset_datasink_name, asset_dataset):
        now = datetime.now(timezone.utc)
        job_id = uuid4()
        job = {
            "id": job_id, "org_id": org_id, "user_id": user_id,
            "datasource_type": datasource_type, "datasource_ref": datasource_ref,
            "datasource_filter": datasource_filter or "{}",
            "datasink_name": datasink_name, "destination_dataset": destination_dataset,
            "asset_resolution": asset_resolution or False,
            "asset_url_fields": asset_url_fields or "[]",
            "asset_url_prefix": asset_url_prefix or "",
            "asset_datasink_name": asset_datasink_name,
            "asset_dataset": asset_dataset,
            "status": "pending", "records_total": None,
            "records_processed": 0, "records_skipped": 0, "asset_errors": 0,
            "error_message": None, "created_at": now, "started_at": None,
            "completed_at": None, "last_heartbeat_at": None,
        }
        self._jobs[str(job_id)] = job
        return job

    async def fetchrow(self, query: str, *args):
        q = query.strip().upper()
        if "INSERT INTO EXPORT_JOBS" in q:
            return self._make_job(*args)
        if "SELECT * FROM EXPORT_JOBS WHERE ID" in q:
            return self._jobs.get(str(args[0]))
        return None

    async def fetch(self, query: str, *args):
        return list(self._jobs.values())

    async def fetchval(self, query: str, *args):
        return 0  # no active jobs

    async def execute(self, query: str, *args):
        return "OK"

    async def close(self):
        pass

    def acquire(self):
        return _FakeConn(self)


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def transaction(self):
        return _FakeTx()

    async def execute(self, *args):
        return "OK"

    async def fetchval(self, query, *args):
        return 0


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def client(config_with_sinks, monkeypatch):
    from databridge.config import get_settings
    get_settings.cache_clear()
    pool = _ExportPool()
    _arq_mock = MagicMock(enqueue_job=AsyncMock(), aclose=AsyncMock())
    with patch("databridge.main.create_pool", AsyncMock(return_value=pool)), \
         patch("arq.create_pool", AsyncMock(return_value=_arq_mock)):
        from databridge.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    get_settings.cache_clear()


@pytest.fixture
def export_payload():
    return {
        "datasource_type": "system",
        "datasource_ref": "test-source",
        "datasink_name": SINK_NAME,
        "destination_dataset": "test_dataset",
    }


def test_create_export_job_returns_201(client, export_payload):
    """POST /api/v1/export-jobs returns 201 with ExportJobResponse."""
    resp = client.post(
        "/api/v1/export-jobs", json=export_payload,
        headers={"X-Group-ID": "org1/user1"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "id" in body
    assert body["status"] == "pending"
    assert body["records_total"] is None


def test_create_export_job_pending_status(client, export_payload):
    """Returned job has status=pending, records_total=null."""
    resp = client.post(
        "/api/v1/export-jobs", json=export_payload,
        headers={"X-Group-ID": "org1/user1"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["records_total"] is None


def test_get_export_job_returns_200(client, export_payload):
    """GET /api/v1/export-jobs/{id} returns 200."""
    create_resp = client.post(
        "/api/v1/export-jobs", json=export_payload,
        headers={"X-Group-ID": "org1/user1"},
    )
    assert create_resp.status_code == 201
    job_id = create_resp.json()["id"]
    get_resp = client.get(
        f"/api/v1/export-jobs/{job_id}",
        headers={"X-Group-ID": "org1/user1"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == job_id


def test_unknown_datasink_name_returns_400(client):
    """Unknown datasink_name returns 400."""
    payload = {
        "datasource_type": "system",
        "datasource_ref": "src",
        "datasink_name": "nonexistent-sink",
        "destination_dataset": "ds",
    }
    resp = client.post(
        "/api/v1/export-jobs", json=payload,
        headers={"X-Group-ID": "org1/user1"},
    )
    assert resp.status_code == 400


def test_org_over_concurrent_limit_returns_429(client, export_payload, monkeypatch):
    """Org over concurrent limit returns 429 with informative message."""
    import databridge.routes.export_jobs as ej_routes

    async def _always_over_limit(pool, org_id):
        return 999

    monkeypatch.setattr(ej_routes, "count_active_jobs_for_org", _always_over_limit)
    resp = client.post(
        "/api/v1/export-jobs", json=export_payload,
        headers={"X-Group-ID": "org1/user1"},
    )
    assert resp.status_code == 429
    assert "concurrent" in resp.json()["detail"].lower()
