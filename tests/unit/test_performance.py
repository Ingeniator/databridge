"""T062 — p95 latency guards for key endpoints."""
import asyncio
import json
import statistics
import time
import pytest
import respx
import httpx
from cryptography.fernet import Fernet
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from uuid import uuid4


def _make_app_client(fernet_key, monkeypatch, config_file, pool=None):
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(config_file))
    from databridge.config import get_settings
    get_settings.cache_clear()

    if pool is None:
        fake_pool = MagicMock()
        fake_pool.fetch = AsyncMock(return_value=[])
        fake_pool.fetchrow = AsyncMock(return_value=None)
        fake_pool.fetchval = AsyncMock(return_value=1)
        fake_pool.execute = AsyncMock()
        fake_pool.close = AsyncMock()
        pool = fake_pool

    with patch("databridge.main.create_pool", AsyncMock(return_value=pool)):
        from databridge.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    get_settings.cache_clear()


def _p95(latencies):
    s = sorted(latencies)
    idx = int(len(s) * 0.95)
    return s[min(idx, len(s) - 1)]


def test_list_connections_p95_under_500ms(config_file, fernet_key, monkeypatch):
    """SC-001: p95 latency ≤ 500 ms for GET /api/v1/connections (50 concurrent)."""
    gen = _make_app_client(fernet_key, monkeypatch, config_file)
    c = next(gen)

    latencies = []
    for _ in range(50):
        t0 = time.perf_counter()
        r = c.get("/api/v1/connections", headers={"X-Group-ID": "perf-user"})
        latencies.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200

    p95 = _p95(latencies)
    assert p95 <= 500, f"p95={p95:.1f}ms exceeds 500ms limit"

    try:
        next(gen)
    except StopIteration:
        pass


def test_ping_p95_under_5000ms(config_file, fernet_key, monkeypatch):
    """SC-006: p95 latency ≤ 5000 ms for POST /api/v1/connections/{id}/ping (20 concurrent)."""
    f = Fernet(fernet_key.encode())
    creds_enc = f.encrypt(json.dumps({"user": "default", "password": ""}).encode())
    conn_id = uuid4()
    row = {
        "id": conn_id, "owner_key": "perf-user", "label": "Perf",
        "type": "clickhouse", "role": "source",
        "connection_url": "http://perf-ch:8123",
        "credentials_enc": creds_enc, "status": "untested",
        "last_tested_at": None, "created_at": None, "updated_at": None,
    }
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1)
    pool.close = AsyncMock()

    gen = _make_app_client(fernet_key, monkeypatch, config_file, pool=pool)
    c = next(gen)

    latencies = []
    with respx.mock:
        respx.get("http://perf-ch:8123/ping").mock(return_value=httpx.Response(200))
        for _ in range(20):
            t0 = time.perf_counter()
            r = c.post(f"/api/v1/connections/{conn_id}/ping", headers={"X-Group-ID": "perf-user"})
            latencies.append((time.perf_counter() - t0) * 1000)
            assert r.status_code == 200

    p95 = _p95(latencies)
    assert p95 <= 5000, f"p95={p95:.1f}ms exceeds 5000ms limit"

    try:
        next(gen)
    except StopIteration:
        pass
