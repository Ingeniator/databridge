"""T018/T031 — Integration tests for export jobs with masking/sampling/webhook fields."""
import json
import textwrap
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

SINK_NAME = "int-test-sink"


@pytest.fixture
def config_masking(tmp_path, monkeypatch):
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


class _MaskingPool:
    """Fake pool that stores new masking/sampling/webhook columns."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}

    def _make_job(self, org_id, user_id, datasource_type, datasource_ref,
                  datasource_filter, datasink_name, destination_dataset,
                  asset_resolution, asset_url_fields, asset_url_prefix,
                  asset_datasink_name, asset_dataset,
                  masking_rules, sampling_config, webhook_url, webhook_enabled,
                  webhook_payload_template):
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
            "masking_rules": masking_rules,
            "sampling_config": sampling_config,
            "webhook_url": webhook_url,
            "webhook_enabled": webhook_enabled or False,
            "webhook_payload_template": webhook_payload_template,
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
        return 0

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
def masking_client(config_masking, monkeypatch):
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(config_masking))
    from databridge.config import get_settings
    get_settings.cache_clear()

    pool = _MaskingPool()
    with patch("databridge.main.create_pool", AsyncMock(return_value=pool)):
        from databridge.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c, pool
    get_settings.cache_clear()


def test_create_job_with_masking_rules(masking_client):
    c, pool = masking_client
    r = c.post(
        "/api/v1/export-jobs",
        json={
            "datasource_type": "system",
            "datasource_ref": "fake-ref",
            "datasink_name": SINK_NAME,
            "destination_dataset": "masked_export",
            "masking_rules": [
                {"field_path": "payload.user_id", "action": "mask"}
            ],
        },
        headers={"X-Group-ID": "testorg"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["masking_rules"] == [{"field_path": "payload.user_id", "action": "mask"}]


def test_create_job_with_sampling_config(masking_client):
    c, pool = masking_client
    r = c.post(
        "/api/v1/export-jobs",
        json={
            "datasource_type": "system",
            "datasource_ref": "fake-ref",
            "datasink_name": SINK_NAME,
            "destination_dataset": "sampled_export",
            "sampling_config": {"method": "random", "ratio_or_size": 0.1},
        },
        headers={"X-Group-ID": "testorg"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sampling_config"]["method"] == "random"
    assert body["sampling_config"]["ratio_or_size"] == 0.1


def test_create_job_with_webhook(masking_client):
    c, pool = masking_client
    r = c.post(
        "/api/v1/export-jobs",
        json={
            "datasource_type": "system",
            "datasource_ref": "fake-ref",
            "datasink_name": SINK_NAME,
            "destination_dataset": "webhook_export",
            "webhook_url": "http://test",
            "webhook_enabled": False,
        },
        headers={"X-Group-ID": "testorg"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["webhook_url"] == "http://test"
    assert body["webhook_enabled"] is False


def test_list_jobs_includes_masking_fields(masking_client):
    c, pool = masking_client
    # Create one job first
    c.post(
        "/api/v1/export-jobs",
        json={
            "datasource_type": "system",
            "datasource_ref": "ref",
            "datasink_name": SINK_NAME,
            "destination_dataset": "ds",
            "masking_rules": [{"field_path": "email", "action": "redact"}],
        },
        headers={"X-Group-ID": "testorg"},
    )
    r = c.get("/api/v1/export-jobs", headers={"X-Group-ID": "testorg"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) >= 1
    for item in items:
        assert "masking_rules" in item
        assert "sampling_config" in item
        assert "webhook_url" in item
        assert "webhook_enabled" in item


# ── T031 — retry preserves masking/sampling/webhook config ──────────────────

def test_retry_preserves_masking_sampling_webhook(masking_client):
    c, pool = masking_client
    # Create a failed job with all new fields
    create_r = c.post(
        "/api/v1/export-jobs",
        json={
            "datasource_type": "system",
            "datasource_ref": "ref",
            "datasink_name": SINK_NAME,
            "destination_dataset": "retry_ds",
            "masking_rules": [{"field_path": "ssn", "action": "hash"}],
            "sampling_config": {"method": "systematic", "ratio_or_size": 0.5},
            "webhook_url": "http://hook.example.com",
            "webhook_enabled": True,
        },
        headers={"X-Group-ID": "testorg"},
    )
    assert create_r.status_code == 201
    job_id = create_r.json()["id"]

    # Manually set job status to failed in pool
    pool._jobs[job_id]["status"] = "failed"

    retry_r = c.post(
        f"/api/v1/export-jobs/{job_id}/retry",
        headers={"X-Group-ID": "testorg"},
    )
    assert retry_r.status_code == 201, retry_r.text
    new_job = retry_r.json()
    assert new_job["masking_rules"] == [{"field_path": "ssn", "action": "hash"}]
    assert new_job["sampling_config"]["method"] == "systematic"
    assert new_job["webhook_url"] == "http://hook.example.com"
    assert new_job["webhook_enabled"] is True
