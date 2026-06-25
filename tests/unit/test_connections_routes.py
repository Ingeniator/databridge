"""Unit tests for routes/connections.py — no real DB or network."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge.auth import AuthContext
from databridge.routes.connections import router

FAKE_AUTH = AuthContext(public_key="org/user", org_id="org", user_id="user")
CONN_ID = uuid.uuid4()


def _row(**overrides):
    base = {
        "id": CONN_ID,
        "label": "Test DB",
        "type": "clickhouse",
        "role": "source",
        "connection_url": "https://ch.example.com",
        "status": "untested",
        "credentials_enc": b"\x00" * 32,
        "last_tested_at": None,
        "created_at": None,
        "updated_at": None,
    }
    base.update(overrides)
    return base


def _mock_settings(datasources=()):
    s = MagicMock()
    s.datasources = list(datasources)
    return s


@pytest.fixture()
def mock_pool():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


@pytest.fixture()
def app(mock_pool):
    from databridge.auth import get_auth
    from databridge.db.pool import get_pool
    from databridge.routes.deps import get_system_sources

    _app = FastAPI()
    _app.include_router(router)
    _app.dependency_overrides[get_auth] = lambda: FAKE_AUTH
    _app.dependency_overrides[get_pool] = lambda: mock_pool
    _app.dependency_overrides[get_system_sources] = lambda: []
    return _app


@pytest.fixture()
def client(app):
    return TestClient(app, raise_server_exceptions=True)


# ── POST /connections/test ────────────────────────────────────────────────────

def test_test_connection_reachable_auth_ok(client):
    mock_adapter = MagicMock()
    mock_adapter.ping = AsyncMock()
    mock_adapter.schema = AsyncMock(return_value=({"f": {"type": "string"}}, 0))
    with patch("databridge.routes.connections.get_adapter", return_value=mock_adapter):
        r = client.post("/api/v1/connections/test", json={
            "type": "clickhouse",
            "connection_url": "https://ch.example.com",
            "credentials": {"user": "u", "password": "p", "database": "d", "table": "t"},
        })
    assert r.status_code == 200
    assert r.json()["status"] == "reachable"
    assert r.json()["auth_ok"] is True


def test_test_connection_ping_fails(client):
    mock_adapter = MagicMock()
    mock_adapter.ping = AsyncMock(side_effect=ConnectionError("refused"))
    with patch("databridge.routes.connections.get_adapter", return_value=mock_adapter):
        r = client.post("/api/v1/connections/test", json={
            "type": "clickhouse",
            "connection_url": "https://ch.example.com",
            "credentials": {"user": "u", "password": "p", "database": "d", "table": "t"},
        })
    assert r.status_code == 200
    assert r.json()["status"] == "unreachable"


def test_test_connection_schema_fails_auth_not_ok(client):
    mock_adapter = MagicMock()
    mock_adapter.ping = AsyncMock()
    mock_adapter.schema = AsyncMock(side_effect=PermissionError("denied"))
    with patch("databridge.routes.connections.get_adapter", return_value=mock_adapter):
        r = client.post("/api/v1/connections/test", json={
            "type": "clickhouse",
            "connection_url": "https://ch.example.com",
            "credentials": {"user": "u", "password": "p", "database": "d", "table": "t"},
        })
    assert r.status_code == 200
    assert r.json()["status"] == "reachable"
    assert r.json()["auth_ok"] is False


# ── POST /connections ─────────────────────────────────────────────────────────

def test_create_connection(client):
    with (
        patch("databridge.routes.connections.encrypt_credentials", return_value=b"enc"),
        patch("databridge.routes.connections.insert_connection", new_callable=AsyncMock) as mock_insert,
    ):
        mock_insert.return_value = _row()
        r = client.post("/api/v1/connections", json={
            "label": "Test DB",
            "type": "clickhouse",
            "role": "source",
            "connection_url": "https://ch.example.com",
            "credentials": {"user": "u", "password": "p", "database": "default", "table": "events"},
        })
    assert r.status_code == 201
    assert r.json()["label"] == "Test DB"
    assert r.json()["system"] is False


# ── GET /connections ──────────────────────────────────────────────────────────

def test_list_connections_empty(client):
    with patch("databridge.routes.connections.list_connections", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = []
        r = client.get("/api/v1/connections")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_list_connections_returns_db_rows(client):
    with patch("databridge.routes.connections.list_connections", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = [_row()]
        r = client.get("/api/v1/connections")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1
    assert r.json()["items"][0]["label"] == "Test DB"


def test_list_connections_includes_system_sources(app):
    from databridge.routes.deps import get_system_sources

    src = MagicMock()
    src.id = uuid.uuid4()
    src.name = "Sys Source"
    src.type = "clickhouse"
    src.url = "https://sys.example.com"
    src.endpoint = ""
    app.dependency_overrides[get_system_sources] = lambda: [src]

    with patch("databridge.routes.connections.list_connections", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = []
        r = TestClient(app).get("/api/v1/connections")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["system"] is True
    assert items[0]["label"] == "Sys Source"


# ── GET /connections/{id} ─────────────────────────────────────────────────────

def test_get_one_connection(mock_pool, client):
    mock_pool.fetchrow.return_value = _row()
    r = client.get(f"/api/v1/connections/{CONN_ID}")
    assert r.status_code == 200
    assert r.json()["label"] == "Test DB"


def test_get_one_connection_not_found(mock_pool, client):
    mock_pool.fetchrow.return_value = None
    r = client.get(f"/api/v1/connections/{CONN_ID}")
    assert r.status_code == 404


# ── GET /connections/{id}/credentials ────────────────────────────────────────

def test_get_credentials_hides_secret_keys(mock_pool, client):
    mock_pool.fetchrow.return_value = _row()
    with patch("databridge.routes.connections.decrypt_credentials", return_value={
        "user": "alice",
        "password": "s3cr3t",
        "host": "ch.example.com",
        "secret_key": "sk_...",
    }):
        r = client.get(f"/api/v1/connections/{CONN_ID}/credentials")
    assert r.status_code == 200
    body = r.json()
    assert "user" in body
    assert "host" in body
    assert "password" not in body
    assert "secret_key" not in body


# ── PATCH /connections/{id} ───────────────────────────────────────────────────

def test_patch_connection_label_only(client):
    with (
        patch("databridge.routes.connections.get_settings", return_value=_mock_settings()),
        patch("databridge.routes.connections.update_connection", new_callable=AsyncMock) as mock_update,
    ):
        mock_update.return_value = _row(label="New Label")
        r = client.patch(f"/api/v1/connections/{CONN_ID}", json={"label": "New Label"})
    assert r.status_code == 200
    assert r.json()["label"] == "New Label"


def test_patch_connection_not_found(client):
    with (
        patch("databridge.routes.connections.get_settings", return_value=_mock_settings()),
        patch("databridge.routes.connections.update_connection", new_callable=AsyncMock) as mock_update,
    ):
        mock_update.return_value = None
        r = client.patch(f"/api/v1/connections/{CONN_ID}", json={"label": "x"})
    assert r.status_code == 404


def test_patch_system_source_returns_404(client):
    sys_src = MagicMock()
    sys_src.id = CONN_ID
    with patch("databridge.routes.connections.get_settings", return_value=_mock_settings([sys_src])):
        r = client.patch(f"/api/v1/connections/{CONN_ID}", json={"label": "x"})
    assert r.status_code == 404


def test_patch_merges_credentials(client):
    existing = {"user": "alice", "password": "old", "database": "db", "table": "t"}
    with (
        patch("databridge.routes.connections.get_settings", return_value=_mock_settings()),
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value=existing),
        patch("databridge.routes.connections.encrypt_credentials", return_value=b"enc") as mock_enc,
        patch("databridge.routes.connections.update_connection", new_callable=AsyncMock) as mock_update,
    ):
        mock_get.return_value = _row()
        mock_update.return_value = _row()
        r = client.patch(f"/api/v1/connections/{CONN_ID}", json={
            "credentials": {"user": "alice", "password": "new", "database": "db", "table": "t"},
        })
    assert r.status_code == 200
    merged = mock_enc.call_args[0][0]
    assert merged["password"] == "new"


def test_patch_credentials_get_connection_not_found(client):
    with (
        patch("databridge.routes.connections.get_settings", return_value=_mock_settings()),
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
    ):
        mock_get.return_value = None
        r = client.patch(f"/api/v1/connections/{CONN_ID}", json={
            "credentials": {"user": "u", "password": "p", "database": "d", "table": "t"},
        })
    assert r.status_code == 404


# ── DELETE /connections/{id} ──────────────────────────────────────────────────

def test_delete_connection_success(client):
    with (
        patch("databridge.routes.connections.get_settings", return_value=_mock_settings()),
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.count_referencing_jobs", new_callable=AsyncMock) as mock_count,
        patch("databridge.routes.connections.delete_connection", new_callable=AsyncMock),
    ):
        mock_get.return_value = _row()
        mock_count.return_value = 0
        r = client.delete(f"/api/v1/connections/{CONN_ID}")
    assert r.status_code == 204


def test_delete_connection_not_found(client):
    with (
        patch("databridge.routes.connections.get_settings", return_value=_mock_settings()),
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
    ):
        mock_get.return_value = None
        r = client.delete(f"/api/v1/connections/{CONN_ID}")
    assert r.status_code == 404


def test_delete_connection_in_use(client):
    with (
        patch("databridge.routes.connections.get_settings", return_value=_mock_settings()),
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.count_referencing_jobs", new_callable=AsyncMock) as mock_count,
    ):
        mock_get.return_value = _row()
        mock_count.return_value = 3
        r = client.delete(f"/api/v1/connections/{CONN_ID}")
    assert r.status_code == 409
    assert "3" in r.json()["detail"]


def test_delete_system_source_returns_404(client):
    sys_src = MagicMock()
    sys_src.id = CONN_ID
    with patch("databridge.routes.connections.get_settings", return_value=_mock_settings([sys_src])):
        r = client.delete(f"/api/v1/connections/{CONN_ID}")
    assert r.status_code == 404


# ── POST /connections/{id}/ping ───────────────────────────────────────────────

def test_ping_connection_reachable(client):
    mock_adapter = MagicMock()
    mock_adapter.ping = AsyncMock()
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
        patch("databridge.routes.connections.update_connection_status", new_callable=AsyncMock),
    ):
        mock_get.return_value = _row()
        r = client.post(f"/api/v1/connections/{CONN_ID}/ping")
    assert r.status_code == 200
    assert r.json()["status"] == "reachable"


def test_ping_connection_unreachable(client):
    mock_adapter = MagicMock()
    mock_adapter.ping = AsyncMock(side_effect=ConnectionError("refused"))
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
        patch("databridge.routes.connections.update_connection_status", new_callable=AsyncMock),
    ):
        mock_get.return_value = _row()
        r = client.post(f"/api/v1/connections/{CONN_ID}/ping")
    assert r.status_code == 200
    assert r.json()["status"] == "unreachable"
    assert r.json()["error"] is not None


def test_ping_connection_not_found(client):
    with patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        r = client.post(f"/api/v1/connections/{CONN_ID}/ping")
    assert r.status_code == 404


# ── POST /connections/{id}/preview ────────────────────────────────────────────

def test_preview_connection_success(client):
    mock_adapter = MagicMock()
    mock_adapter.preview = AsyncMock(return_value=[{"id": 1, "name": "Alice"}])
    mock_adapter.count = AsyncMock(return_value=42)
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
        patch("databridge.routes.connections._infer_schema", return_value={}),
    ):
        mock_get.return_value = _row()
        r = client.post(f"/api/v1/connections/{CONN_ID}/preview", json={})
    assert r.status_code == 200
    assert r.json()["results"] == [{"id": 1, "name": "Alice"}]
    assert r.json()["total_count"] == 42


def test_preview_sink_connection_returns_400(client):
    with patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _row(role="sink")
        r = client.post(f"/api/v1/connections/{CONN_ID}/preview", json={})
    assert r.status_code == 400


def test_preview_connection_not_found(client):
    with patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        r = client.post(f"/api/v1/connections/{CONN_ID}/preview", json={})
    assert r.status_code == 404


def test_preview_not_implemented_returns_501(client):
    mock_adapter = MagicMock()
    mock_adapter.preview = AsyncMock(side_effect=NotImplementedError)
    mock_adapter.count = AsyncMock(return_value=0)
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
    ):
        mock_get.return_value = _row()
        r = client.post(f"/api/v1/connections/{CONN_ID}/preview", json={})
    assert r.status_code == 501


def test_preview_error_returns_502(client):
    mock_adapter = MagicMock()
    mock_adapter.preview = AsyncMock(side_effect=RuntimeError("upstream down"))
    mock_adapter.count = AsyncMock(return_value=0)
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
    ):
        mock_get.return_value = _row()
        r = client.post(f"/api/v1/connections/{CONN_ID}/preview", json={})
    assert r.status_code == 502


# ── GET /connections/{id}/schema ──────────────────────────────────────────────

def test_schema_connection_success(client):
    mock_adapter = MagicMock()
    mock_adapter.schema = AsyncMock(return_value=(
        {"name": {"type": "string", "example": "Alice"}}, 100
    ))
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
    ):
        mock_get.return_value = _row()
        r = client.get(f"/api/v1/connections/{CONN_ID}/schema")
    assert r.status_code == 200
    assert "name" in r.json()["fields"]
    assert r.json()["sample_count"] == 100


def test_schema_connection_not_found(client):
    with patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        r = client.get(f"/api/v1/connections/{CONN_ID}/schema")
    assert r.status_code == 404


def test_schema_sink_returns_400(client):
    with patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _row(role="sink")
        r = client.get(f"/api/v1/connections/{CONN_ID}/schema")
    assert r.status_code == 400


def test_schema_not_implemented_returns_501(client):
    mock_adapter = MagicMock()
    mock_adapter.schema = AsyncMock(side_effect=NotImplementedError)
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
    ):
        mock_get.return_value = _row()
        r = client.get(f"/api/v1/connections/{CONN_ID}/schema")
    assert r.status_code == 501


def test_schema_error_returns_502(client):
    mock_adapter = MagicMock()
    mock_adapter.schema = AsyncMock(side_effect=RuntimeError("upstream error"))
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
    ):
        mock_get.return_value = _row()
        r = client.get(f"/api/v1/connections/{CONN_ID}/schema")
    assert r.status_code == 502


# ── GET /connections/{id}/pii-fields ─────────────────────────────────────────

def test_pii_fields_returns_email_candidate(client):
    mock_adapter = MagicMock()
    mock_adapter.schema = AsyncMock(return_value=(
        {"email": {"type": "string"}, "name": {"type": "string"}}, 10
    ))
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
    ):
        mock_get.return_value = _row()
        r = client.get(f"/api/v1/connections/{CONN_ID}/pii-fields")
    assert r.status_code == 200
    assert "email" in r.json()["candidate_fields"]


def test_pii_fields_schema_error_returns_empty(client):
    mock_adapter = MagicMock()
    mock_adapter.schema = AsyncMock(side_effect=RuntimeError("no schema"))
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
    ):
        mock_get.return_value = _row()
        r = client.get(f"/api/v1/connections/{CONN_ID}/pii-fields")
    assert r.status_code == 200
    assert r.json()["candidate_fields"] == []


def test_pii_fields_not_found(client):
    with patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        r = client.get(f"/api/v1/connections/{CONN_ID}/pii-fields")
    assert r.status_code == 404


# ── POST /connections/{id}/test-asset-resolution ──────────────────────────────

def test_asset_resolution_not_found(client):
    with patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        r = client.post(f"/api/v1/connections/{CONN_ID}/test-asset-resolution", json={
            "url_fields": ["image_url"],
            "url_prefix": "",
        })
    assert r.status_code == 404


def test_asset_resolution_no_url_values_returns_empty(client):
    mock_adapter = MagicMock()
    mock_adapter.preview = AsyncMock(return_value=[{"id": 1, "image_url": "not_a_url"}])
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
    ):
        mock_get.return_value = _row()
        r = client.post(f"/api/v1/connections/{CONN_ID}/test-asset-resolution", json={
            "url_fields": ["image_url"],
            "url_prefix": "",
        })
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_asset_resolution_preview_error_returns_502(client):
    mock_adapter = MagicMock()
    mock_adapter.preview = AsyncMock(side_effect=RuntimeError("db down"))
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
    ):
        mock_get.return_value = _row()
        r = client.post(f"/api/v1/connections/{CONN_ID}/test-asset-resolution", json={
            "url_fields": ["image_url"],
            "url_prefix": "",
        })
    assert r.status_code == 502


def test_asset_resolution_head_request_ok(client):
    mock_adapter = MagicMock()
    mock_adapter.preview = AsyncMock(return_value=[{"image_url": "https://cdn.example.com/img.png"}])
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
        respx.mock,
    ):
        respx.head("https://cdn.example.com/img.png").mock(return_value=httpx.Response(200))
        mock_get.return_value = _row()
        r = client.post(f"/api/v1/connections/{CONN_ID}/test-asset-resolution", json={
            "url_fields": ["image_url"],
            "url_prefix": "",
        })
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 1
    assert results[0]["ok"] is True
    assert results[0]["status_code"] == 200


def test_asset_resolution_head_request_404(client):
    mock_adapter = MagicMock()
    mock_adapter.preview = AsyncMock(return_value=[{"image_url": "https://cdn.example.com/missing.png"}])
    with (
        patch("databridge.routes.connections.get_connection", new_callable=AsyncMock) as mock_get,
        patch("databridge.routes.connections.decrypt_credentials", return_value={}),
        patch("databridge.routes.connections.get_adapter", return_value=mock_adapter),
        respx.mock,
    ):
        respx.head("https://cdn.example.com/missing.png").mock(return_value=httpx.Response(404))
        mock_get.return_value = _row()
        r = client.post(f"/api/v1/connections/{CONN_ID}/test-asset-resolution", json={
            "url_fields": ["image_url"],
            "url_prefix": "",
        })
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert results[0]["status_code"] == 404
