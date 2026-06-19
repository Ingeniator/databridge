import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from uuid import UUID

_CONN_ID = "00000000-0000-0000-0000-000000000001"

_MOCK_ROW = {
    "id": UUID(_CONN_ID),
    "label": "test",
    "type": "clickhouse",
    "role": "source",
    "connection_url": "http://clickhouse:8123",
    "status": "untested",
    "credentials_enc": b"enc",
    "last_tested_at": None,
    "created_at": None,
    "updated_at": None,
}


def _make_pool(row=_MOCK_ROW):
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=1)
    pool.fetchrow = AsyncMock(return_value=row)
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


def test_test_endpoint_reachable_with_good_creds(client):
    with respx.mock:
        respx.get("http://clickhouse:8123/ping").mock(return_value=httpx.Response(200))
        respx.get("http://clickhouse:8123/").mock(return_value=httpx.Response(200, text=""))
        r = client.post(
            "/api/v1/connections/test",
            json={
                "type": "clickhouse",
                "connection_url": "http://clickhouse:8123",
                "credentials": {"user": "default", "password": "correct"},
            },
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "reachable"
    assert body["auth_ok"] is True
    assert body["auth_error"] is None


def test_test_endpoint_reachable_with_bad_creds(client):
    with respx.mock:
        respx.get("http://clickhouse:8123/ping").mock(return_value=httpx.Response(200))
        respx.get("http://clickhouse:8123/").mock(return_value=httpx.Response(401, text="Authentication failed"))
        r = client.post(
            "/api/v1/connections/test",
            json={
                "type": "clickhouse",
                "connection_url": "http://clickhouse:8123",
                "credentials": {"user": "default", "password": "wrong"},
            },
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "reachable"
    assert body["auth_ok"] is False
    assert body["auth_error"] is not None


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


# ── Asset resolution tests ────────────────────────────────────────────────────

def _make_asset_client(config_file, monkeypatch, pool=None):
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(config_file))
    from databridge.config import get_settings
    get_settings.cache_clear()
    p = pool or _make_pool()
    with patch("databridge.main.create_pool", AsyncMock(return_value=p)):
        from databridge.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    get_settings.cache_clear()


@pytest.fixture
def asset_client(config_file, monkeypatch):
    yield from _make_asset_client(config_file, monkeypatch)


@pytest.fixture
def asset_client_no_row(config_file, monkeypatch):
    yield from _make_asset_client(config_file, monkeypatch, pool=_make_pool(row=None))


def _mock_adapter(records):
    adapter = MagicMock()
    adapter.preview = AsyncMock(return_value=records)
    return adapter


def test_asset_resolution_ok(asset_client):
    records = [{"image_url": "http://cdn.example.com/img1.jpg", "name": "foo"}]
    adapter = _mock_adapter(records)
    with (
        patch("databridge.routes.connections.decrypt_credentials", return_value={"user": "u"}),
        patch("databridge.routes.connections.get_adapter", return_value=adapter),
        respx.mock,
    ):
        respx.head("http://cdn.example.com/img1.jpg").mock(return_value=httpx.Response(200))
        r = asset_client.post(
            f"/api/v1/connections/{_CONN_ID}/test-asset-resolution",
            json={"url_fields": ["image_url"]},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200
    body = r.json()
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["field"] == "image_url"
    assert result["raw_value"] == "http://cdn.example.com/img1.jpg"
    assert result["resolved_url"] == "http://cdn.example.com/img1.jpg"
    assert result["status_code"] == 200
    assert result["ok"] is True
    assert result["error"] is None


def test_asset_resolution_with_prefix(asset_client):
    records = [{"path": "/assets/img.png"}]
    adapter = _mock_adapter(records)
    with (
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=adapter),
        respx.mock,
    ):
        # path doesn't match URL regex, so no results expected
        r = asset_client.post(
            f"/api/v1/connections/{_CONN_ID}/test-asset-resolution",
            json={"url_fields": ["path"], "url_prefix": "https://cdn.example.com"},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_asset_resolution_url_prefix_applied(asset_client):
    records = [{"img": "http://cdn.example.com/photo.jpg"}]
    adapter = _mock_adapter(records)
    with (
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=adapter),
        respx.mock,
    ):
        respx.head("https://proxy.example.com/http://cdn.example.com/photo.jpg").mock(
            return_value=httpx.Response(302)
        )
        r = asset_client.post(
            f"/api/v1/connections/{_CONN_ID}/test-asset-resolution",
            json={"url_fields": ["img"], "url_prefix": "https://proxy.example.com/"},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 1
    assert results[0]["resolved_url"] == "https://proxy.example.com/http://cdn.example.com/photo.jpg"
    assert results[0]["ok"] is True


def test_asset_resolution_url_not_ok(asset_client):
    records = [{"img": "http://cdn.example.com/missing.jpg"}]
    adapter = _mock_adapter(records)
    with (
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=adapter),
        respx.mock,
    ):
        respx.head("http://cdn.example.com/missing.jpg").mock(return_value=httpx.Response(404))
        r = asset_client.post(
            f"/api/v1/connections/{_CONN_ID}/test-asset-resolution",
            json={"url_fields": ["img"]},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["status_code"] == 404
    assert result["ok"] is False


def test_asset_resolution_network_error(asset_client):
    records = [{"img": "http://cdn.example.com/photo.jpg"}]
    adapter = _mock_adapter(records)
    with (
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=adapter),
        respx.mock,
    ):
        respx.head("http://cdn.example.com/photo.jpg").mock(side_effect=httpx.ConnectError("refused"))
        r = asset_client.post(
            f"/api/v1/connections/{_CONN_ID}/test-asset-resolution",
            json={"url_fields": ["img"]},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["ok"] is False
    assert result["error"] is not None


def test_asset_resolution_no_urls_in_records(asset_client):
    records = [{"name": "foo", "count": 42}]
    adapter = _mock_adapter(records)
    with (
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=adapter),
    ):
        r = asset_client.post(
            f"/api/v1/connections/{_CONN_ID}/test-asset-resolution",
            json={"url_fields": ["name"]},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_asset_resolution_connection_not_found(asset_client_no_row):
    r = asset_client_no_row.post(
        f"/api/v1/connections/{_CONN_ID}/test-asset-resolution",
        json={"url_fields": ["img"]},
        headers={"X-Group-ID": "u1"},
    )
    assert r.status_code == 404


def test_asset_resolution_preview_fails(asset_client):
    adapter = MagicMock()
    adapter.preview = AsyncMock(side_effect=RuntimeError("db is down"))
    with (
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=adapter),
    ):
        r = asset_client.post(
            f"/api/v1/connections/{_CONN_ID}/test-asset-resolution",
            json={"url_fields": ["img"]},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 502


def test_asset_resolution_no_auth(asset_client):
    r = asset_client.post(
        f"/api/v1/connections/{_CONN_ID}/test-asset-resolution",
        json={"url_fields": ["img"]},
    )
    assert r.status_code == 401


def test_asset_resolution_caps_at_two_per_field(asset_client):
    records = [
        {"img": "http://cdn.example.com/a.jpg"},
        {"img": "http://cdn.example.com/b.jpg"},
        {"img": "http://cdn.example.com/c.jpg"},
    ]
    adapter = _mock_adapter(records)
    with (
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=adapter),
        respx.mock,
    ):
        respx.head(url__regex=r"http://cdn\.example\.com/[abc]\.jpg").mock(return_value=httpx.Response(200))
        r = asset_client.post(
            f"/api/v1/connections/{_CONN_ID}/test-asset-resolution",
            json={"url_fields": ["img"]},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200
    assert len(r.json()["results"]) == 2
