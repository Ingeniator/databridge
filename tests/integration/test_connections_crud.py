import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from uuid import uuid4, UUID


CH_CREDS = {"user": "default", "password": "pass", "database": "logs"}
CH_CONN = {
    "label": "Test CH", "type": "clickhouse", "role": "source",
    "connection_url": "http://clickhouse:8123", "credentials": CH_CREDS,
}


class _FakePool:
    """In-memory asyncpg pool simulator for CRUD tests."""

    def __init__(self):
        self._store: dict[str, dict] = {}

    def _new_row(self, owner_key, label, type_, role, connection_url, credentials_enc) -> dict:
        now = datetime.now(timezone.utc)
        row_id = uuid4()
        row = {
            "id": row_id, "owner_key": owner_key, "label": label,
            "type": type_, "role": role, "connection_url": connection_url,
            "credentials_enc": credentials_enc, "status": "untested",
            "last_tested_at": None, "created_at": now, "updated_at": now,
        }
        self._store[str(row_id)] = row
        return row

    async def fetchrow(self, query: str, *args):
        q = query.strip().upper()
        if q.startswith("INSERT"):
            # args: owner_key, label, type, role, connection_url, credentials_enc
            return self._new_row(*args)
        if q.startswith("SELECT") and "connections" in q.lower():
            id_val, owner_key = str(args[0]), str(args[1])
            row = self._store.get(id_val)
            if row and row["owner_key"] == owner_key:
                return row
        return None

    async def fetch(self, query: str, *args):
        owner_key = str(args[0]) if args else None
        return [r for r in self._store.values() if r["owner_key"] == owner_key]

    async def execute(self, query: str, *args):
        q = query.strip().upper()
        if q.startswith("DELETE"):
            id_val = str(args[0])
            self._store.pop(id_val, None)
            return "DELETE 1"
        return "OK"

    async def fetchval(self, query: str, *args):
        return 0  # no referencing sync_jobs

    async def close(self):
        pass


@pytest.fixture
def client(config_file, monkeypatch):
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(config_file))
    from databridge.config import get_settings
    get_settings.cache_clear()
    pool = _FakePool()
    with patch("databridge.main.create_pool", AsyncMock(return_value=pool)):
        from databridge.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c, pool
    get_settings.cache_clear()


def test_create_returns_201_no_credentials(client):
    c, _ = client
    r = c.post("/api/v1/connections", json=CH_CONN, headers={"X-Group-ID": "u1"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert "id" in body
    raw = json.dumps(body)
    assert "pass" not in raw
    assert "credentials" not in raw
    assert body["system"] is False


def test_list_returns_owned_only(client):
    c, _ = client
    c.post("/api/v1/connections", json=CH_CONN, headers={"X-Group-ID": "u1"})
    r = c.get("/api/v1/connections", headers={"X-Group-ID": "u1"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert all(i["system"] is False for i in items)


def test_get_by_id_404_wrong_owner(client):
    c, _ = client
    r = c.post("/api/v1/connections", json=CH_CONN, headers={"X-Group-ID": "u1"})
    conn_id = r.json()["id"]
    r2 = c.get(f"/api/v1/connections/{conn_id}", headers={"X-Group-ID": "u2"})
    assert r2.status_code == 404


def test_delete_returns_204(client):
    c, pool = client
    r = c.post("/api/v1/connections", json=CH_CONN, headers={"X-Group-ID": "u1"})
    conn_id = r.json()["id"]
    # Ensure the row is findable (required by DELETE handler's pre-check)
    r2 = c.delete(f"/api/v1/connections/{conn_id}", headers={"X-Group-ID": "u1"})
    assert r2.status_code == 204


def test_delete_already_deleted_returns_404(client):
    c, _ = client
    fake_id = str(uuid4())
    r = c.delete(f"/api/v1/connections/{fake_id}", headers={"X-Group-ID": "u1"})
    assert r.status_code == 404
