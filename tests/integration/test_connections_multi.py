"""T046 — multi-connection management: list ordering, patch semantics, delete guard."""
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from uuid import uuid4, UUID
from cryptography.fernet import Fernet


class _MultiPool:
    """In-memory pool with ordering and status-reset support."""

    def __init__(self, fernet_key: str):
        self._store: dict[str, dict] = {}
        self._jobs: dict[str, str] = {}  # job_id -> connection_id
        self._fernet_key = fernet_key
        self._counter = 0

    def _new_row(self, owner_key, label, type_, role, connection_url, credentials_enc) -> dict:
        self._counter += 1
        # Older items have earlier created_at so list is DESC ordered
        now = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=self._counter)
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
            return self._new_row(*args)
        if q.startswith("UPDATE"):
            id_val = str(args[1]) if len(args) > 1 else None
            owner_key = str(args[0]) if args else None
            row = self._store.get(id_val)
            if not row or row["owner_key"] != owner_key:
                return None
            # Check what fields are being updated from the query
            if "credentials_enc" in query:
                row["credentials_enc"] = args[2] if len(args) > 2 else row["credentials_enc"]
                row["status"] = "untested"
                row["last_tested_at"] = None
            if "label" in query:
                for i, a in enumerate(args[2:], 2):
                    if isinstance(a, str) and a != "untested":
                        row["label"] = a
                        break
            row["updated_at"] = datetime.now(timezone.utc)
            return row
        if q.startswith("SELECT") and "connections" in q.lower():
            id_val, owner_key = str(args[0]), str(args[1])
            row = self._store.get(id_val)
            if row and row["owner_key"] == owner_key:
                return row
        return None

    async def fetch(self, query: str, *args):
        owner_key = str(args[0]) if args else None
        rows = [r for r in self._store.values() if r["owner_key"] == owner_key]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

    async def execute(self, query: str, *args):
        q = query.strip().upper()
        if q.startswith("DELETE"):
            id_val = str(args[0])
            self._store.pop(id_val, None)
            return "DELETE 1"
        if q.startswith("UPDATE"):
            id_val = str(args[-1])
            row = self._store.get(id_val)
            if row:
                if "status" in query:
                    row["status"] = args[0]
                if "last_tested_at" in query:
                    row["last_tested_at"] = args[1]
            return "UPDATE 1"
        return "OK"

    async def fetchval(self, query: str, *args):
        if "sync_jobs" in query:
            conn_id = str(args[0]) if args else None
            return sum(1 for cid in self._jobs.values() if cid == conn_id)
        return 0

    def add_job(self, connection_id: str):
        job_id = str(uuid4())
        self._jobs[job_id] = connection_id

    async def close(self):
        pass


def _make_conn_body(type_="clickhouse", label="Test", role="source"):
    return {
        "label": label, "type": type_, "role": role,
        "connection_url": f"http://{type_}:8123",
        "credentials": {"user": "default", "password": "pass"},
    }


@pytest.fixture
def multi_client(config_file, fernet_key, monkeypatch):
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(config_file))
    from databridge.config import get_settings
    get_settings.cache_clear()
    pool = _MultiPool(fernet_key)
    with patch("databridge.main.create_pool", AsyncMock(return_value=pool)):
        from databridge.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c, pool
    get_settings.cache_clear()


def test_list_three_connections_ordered_by_created_at_desc(multi_client):
    c, pool = multi_client
    c.post("/api/v1/connections", json=_make_conn_body("clickhouse", "A"), headers={"X-Group-ID": "u1"})
    c.post("/api/v1/connections", json=_make_conn_body("trino", "B"), headers={"X-Group-ID": "u1"})
    c.post("/api/v1/connections", json={
        "label": "C", "type": "langfuse", "role": "source",
        "connection_url": "http://langfuse:3000",
        "credentials": {"public_key": "pk", "secret_key": "sk"},
    }, headers={"X-Group-ID": "u1"})
    r = c.get("/api/v1/connections", headers={"X-Group-ID": "u1"})
    assert r.status_code == 200
    items = [i for i in r.json()["items"] if not i["system"]]
    assert len(items) == 3
    # Most-recently created first (DESC)
    labels = [i["label"] for i in items]
    assert labels == ["C", "B", "A"]


def test_patch_label_only_status_unchanged(multi_client):
    c, pool = multi_client
    r = c.post("/api/v1/connections", json=_make_conn_body("clickhouse", "Original"), headers={"X-Group-ID": "u1"})
    conn_id = r.json()["id"]
    original_status = r.json()["status"]

    r2 = c.patch(f"/api/v1/connections/{conn_id}", json={"label": "Renamed"}, headers={"X-Group-ID": "u1"})
    assert r2.status_code == 200
    assert r2.json()["label"] == "Renamed"
    assert r2.json()["status"] == original_status


def test_patch_credentials_resets_status_to_untested(multi_client):
    c, pool = multi_client
    r = c.post("/api/v1/connections", json=_make_conn_body("clickhouse", "Creds Test"), headers={"X-Group-ID": "u1"})
    conn_id = r.json()["id"]

    row = pool._store[conn_id]
    row["status"] = "reachable"
    row["last_tested_at"] = datetime.now(timezone.utc)

    r2 = c.patch(
        f"/api/v1/connections/{conn_id}",
        json={"credentials": {"user": "new_user", "password": "new_pass"}},
        headers={"X-Group-ID": "u1"},
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "untested"
    assert r2.json()["last_tested_at"] is None


def test_delete_with_referencing_job_returns_409(multi_client):
    c, pool = multi_client
    r = c.post("/api/v1/connections", json=_make_conn_body("clickhouse", "With Job"), headers={"X-Group-ID": "u1"})
    conn_id = r.json()["id"]
    pool.add_job(conn_id)

    r2 = c.delete(f"/api/v1/connections/{conn_id}", headers={"X-Group-ID": "u1"})
    assert r2.status_code == 409
    assert "sync job" in r2.json()["detail"]
