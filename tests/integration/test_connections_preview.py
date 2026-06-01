"""T036 — failing preview integration tests (must fail before T039–T040 impl)."""
import json
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from uuid import uuid4
from cryptography.fernet import Fernet


def _make_row(conn_id, role="source", type_="clickhouse", url="http://ch:8123", fernet_key=None):
    fernet_key = fernet_key or Fernet.generate_key().decode()
    f = Fernet(fernet_key.encode())
    creds_enc = f.encrypt(json.dumps({"user": "default", "password": ""}).encode())
    return {
        "id": conn_id, "owner_key": "u1", "label": "Test",
        "type": type_, "role": role, "connection_url": url,
        "credentials_enc": creds_enc, "status": "untested",
        "last_tested_at": None, "created_at": None, "updated_at": None,
    }, fernet_key


@pytest.fixture
def source_client(config_file, fernet_key, monkeypatch):
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(config_file))
    from databridge.config import get_settings
    get_settings.cache_clear()

    conn_id = uuid4()
    row, _ = _make_row(conn_id, role="source", url="http://ch:8123", fernet_key=fernet_key)

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


@pytest.fixture
def sink_client(config_file, fernet_key, monkeypatch):
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(config_file))
    from databridge.config import get_settings
    get_settings.cache_clear()

    conn_id = uuid4()
    row, _ = _make_row(conn_id, role="sink", type_="dataset", url="http://sink:8010", fernet_key=fernet_key)

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


def test_preview_source_returns_results(source_client):
    c, conn_id = source_client
    with respx.mock:
        respx.get("http://ch:8123/").mock(
            return_value=httpx.Response(200, text='{"id":"1","msg":"hello"}\n{"id":"2","msg":"world"}\n')
        )
        r = c.post(
            f"/api/v1/connections/{conn_id}/preview",
            json={"query": "hello", "limit": 10},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "results" in body
    assert isinstance(body["results"], list)
    assert len(body["results"]) >= 1
    assert body["connection_id"] == conn_id


def test_preview_sink_returns_400(sink_client):
    c, conn_id = sink_client
    r = c.post(
        f"/api/v1/connections/{conn_id}/preview",
        json={"limit": 5},
        headers={"X-Group-ID": "u1"},
    )
    assert r.status_code == 400
    assert "source" in r.json()["detail"].lower()


def test_preview_backend_error_returns_502(source_client):
    c, conn_id = source_client
    with respx.mock:
        respx.get("http://ch:8123/").mock(side_effect=httpx.ConnectError("refused"))
        r = c.post(
            f"/api/v1/connections/{conn_id}/preview",
            json={"limit": 5},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 502
