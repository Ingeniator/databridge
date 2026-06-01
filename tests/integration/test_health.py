import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch


def _pool_mock(fetchval_result=1):
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=fetchval_result)
    pool.close = AsyncMock()
    return pool


def _make_client(config_file, monkeypatch, pool=None):
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(config_file))
    from databridge.config import get_settings
    get_settings.cache_clear()

    if pool is None:
        pool = _pool_mock()

    with patch("databridge.main.create_pool", AsyncMock(return_value=pool)):
        from databridge.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    get_settings.cache_clear()


@pytest.fixture
def client(config_file, monkeypatch):
    yield from _make_client(config_file, monkeypatch)


def test_livez(client):
    r = client.get("/livez")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_ok(client):
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["components"]["db"] == "ok"


def test_ready_degraded_when_db_down(config_file, monkeypatch):
    pool = _pool_mock()
    pool.fetchval = AsyncMock(side_effect=Exception("connection refused"))
    yield_client = _make_client(config_file, monkeypatch, pool=pool)
    c = next(yield_client)
    r = c.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["components"]["db"] == "degraded"
    try:
        next(yield_client)
    except StopIteration:
        pass


def test_health_includes_version(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert "version" in body
    assert body["details"] is None
