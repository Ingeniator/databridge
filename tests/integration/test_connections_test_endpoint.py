import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


def _make_pool():
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=1)
    pool.close = AsyncMock()
    return pool


@pytest.fixture
def client(config_file, monkeypatch):
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(config_file))
    from databridge.config import get_settings
    get_settings.cache_clear()
    with patch("databridge.main.create_pool", AsyncMock(return_value=_make_pool())):
        from databridge.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    get_settings.cache_clear()


def test_test_endpoint_reachable(client):
    with respx.mock:
        respx.get("http://clickhouse:8123/ping").mock(return_value=httpx.Response(200))
        r = client.post(
            "/api/v1/connections/test",
            json={
                "type": "clickhouse",
                "connection_url": "http://clickhouse:8123",
                "credentials": {"user": "default", "password": ""},
            },
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "reachable"


def test_test_endpoint_unreachable(client):
    with respx.mock:
        respx.get("http://bad-host:8123/ping").mock(side_effect=httpx.ConnectError("refused"))
        r = client.post(
            "/api/v1/connections/test",
            json={
                "type": "clickhouse",
                "connection_url": "http://bad-host:8123",
                "credentials": {"user": "default", "password": ""},
            },
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "unreachable"


def test_test_endpoint_missing_type(client):
    r = client.post(
        "/api/v1/connections/test",
        json={"connection_url": "http://host:8123", "credentials": {}},
        headers={"X-Group-ID": "u1"},
    )
    assert r.status_code == 422


def test_test_endpoint_no_auth(client):
    r = client.post(
        "/api/v1/connections/test",
        json={"type": "clickhouse", "connection_url": "http://x:8123", "credentials": {}},
    )
    assert r.status_code == 401
