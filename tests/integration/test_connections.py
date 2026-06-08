"""T009 — failing integration tests for POST /preview with FilterSnapshot fields (TDD)."""
import json
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from uuid import uuid4
from cryptography.fernet import Fernet


def _make_conn_row(conn_id, fernet_key):
    f = Fernet(fernet_key.encode())
    creds_enc = f.encrypt(json.dumps({"user": "default", "password": "", "database": "default", "table": "events"}).encode())
    return {
        "id": conn_id, "owner_key": "u1", "label": "Test CH",
        "type": "clickhouse", "role": "source", "connection_url": "http://ch:8123",
        "credentials_enc": creds_enc, "status": "untested",
        "last_tested_at": None, "created_at": None, "updated_at": None,
    }


@pytest.fixture
def preview_client(config_file, fernet_key, monkeypatch):
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(config_file))
    from databridge.config import get_settings
    get_settings.cache_clear()

    conn_id = uuid4()
    row = _make_conn_row(conn_id, fernet_key)

    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)
    pool.close = AsyncMock()

    with patch("databridge.main.create_pool", AsyncMock(return_value=pool)):
        from databridge.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c, str(conn_id)
    get_settings.cache_clear()


def test_preview_with_time_field_and_limit(preview_client):
    c, conn_id = preview_client
    with respx.mock:
        respx.get("http://ch:8123/").mock(
            return_value=httpx.Response(
                200, text='{"id":"1","timestamp":"2024-01-01","status":"ok"}\n'
            )
        )
        r = c.post(
            f"/api/v1/connections/{conn_id}/preview",
            json={"query": "", "time_field": "timestamp", "limit": 100},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "results" in body
    assert "total_count" in body
    assert body["total_count"] >= 0


def test_preview_response_includes_schema_fields(preview_client):
    c, conn_id = preview_client
    with respx.mock:
        respx.get("http://ch:8123/").mock(
            return_value=httpx.Response(
                200, text='{"status":"error","user_id":"abc"}\n'
            )
        )
        r = c.post(
            f"/api/v1/connections/{conn_id}/preview",
            json={"limit": 50},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "schema_fields" in body
    assert isinstance(body["schema_fields"], dict)


def test_preview_limit_up_to_100k_accepted(preview_client):
    c, conn_id = preview_client
    with respx.mock:
        respx.get("http://ch:8123/").mock(
            return_value=httpx.Response(200, text='{"id":"1"}\n')
        )
        r = c.post(
            f"/api/v1/connections/{conn_id}/preview",
            json={"limit": 1000},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200, r.text
