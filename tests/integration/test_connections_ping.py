import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from uuid import uuid4


@pytest.fixture
def app_with_connection(config_file, monkeypatch, fernet_key):
    """App with one pre-seeded ClickHouse connection in the mock DB."""
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(config_file))
    from databridge.config import get_settings
    get_settings.cache_clear()

    from cryptography.fernet import Fernet
    import json
    f = Fernet(fernet_key.encode())
    creds_enc = f.encrypt(json.dumps({"user": "default", "password": ""}).encode())

    conn_id = uuid4()
    row = {
        "id": conn_id, "owner_key": "u1", "label": "Test CH",
        "type": "clickhouse", "role": "source",
        "connection_url": "http://clickhouse:8123",
        "credentials_enc": creds_enc, "status": "untested",
        "last_tested_at": None, "created_at": None, "updated_at": None,
    }

    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1)
    pool.close = AsyncMock()

    with patch("databridge.main.create_pool", AsyncMock(return_value=pool)):
        from databridge.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c, str(conn_id), pool

    get_settings.cache_clear()


def test_ping_reachable(app_with_connection):
    c, conn_id, pool = app_with_connection
    with respx.mock:
        respx.get("http://clickhouse:8123/ping").mock(return_value=httpx.Response(200))
        r = c.post(f"/api/v1/connections/{conn_id}/ping", headers={"X-Group-ID": "u1"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "reachable"
    assert body["latency_ms"] is not None


def test_ping_unreachable(app_with_connection):
    c, conn_id, pool = app_with_connection
    with respx.mock:
        respx.get("http://clickhouse:8123/ping").mock(side_effect=httpx.ConnectError("refused"))
        r = c.post(f"/api/v1/connections/{conn_id}/ping", headers={"X-Group-ID": "u1"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unreachable"
    assert "error" in body


def test_ping_updates_db_status(app_with_connection):
    c, conn_id, pool = app_with_connection
    with respx.mock:
        respx.get("http://clickhouse:8123/ping").mock(return_value=httpx.Response(200))
        c.post(f"/api/v1/connections/{conn_id}/ping", headers={"X-Group-ID": "u1"})
    pool.execute.assert_called()
