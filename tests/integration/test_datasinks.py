"""Integration test stubs — GET /api/v1/datasinks endpoints."""
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

MOCK_SINK_NAME = "mock-sink"
MOCK_SINK_URL = "http://dataset-mock:8020"


@pytest.fixture
def config_with_sinks(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(textwrap.dedent(f"""
        server:
          port: 5010
          debug: true
          silence_probes: false
        database_url: "postgresql://postgres:postgres@localhost:5432/databridge_test"
        encryption_key: "{key}"
        datasources: []
        datasinks:
          - name: "{MOCK_SINK_NAME}"
            type: dataset-mock
            url: "{MOCK_SINK_URL}"
    """))
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(cfg))
    yield cfg


@pytest.fixture
def client(config_with_sinks, monkeypatch):
    from databridge.config import get_settings
    get_settings.cache_clear()
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=0)
    pool.close = AsyncMock()
    _arq_mock = MagicMock(enqueue_job=AsyncMock(), aclose=AsyncMock())
    with patch("databridge.main.create_pool", AsyncMock(return_value=pool)), \
         patch("arq.create_pool", AsyncMock(return_value=_arq_mock)):
        from databridge.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    get_settings.cache_clear()


def test_get_datasinks_returns_configured(client):
    """GET /api/v1/datasinks returns configured sinks."""
    resp = client.get("/api/v1/datasinks", headers={"X-Group-ID": "org1/user1"})
    assert resp.status_code == 200
    body = resp.json()
    assert "datasinks" in body
    assert any(s["name"] == MOCK_SINK_NAME for s in body["datasinks"])


def test_get_datasink_datasets_returns_list(client):
    """GET /api/v1/datasinks/{name}/datasets returns list."""
    with respx.mock:
        respx.get(f"{MOCK_SINK_URL}/datasets").mock(
            return_value=httpx.Response(200, json={"datasets": ["ds1", "ds2"]})
        )
        resp = client.get(
            f"/api/v1/datasinks/{MOCK_SINK_NAME}/datasets",
            headers={"X-Group-ID": "org1/user1"},
        )
    assert resp.status_code == 200
    assert resp.json()["datasets"] == ["ds1", "ds2"]


def test_unknown_sink_name_returns_404(client):
    """Unknown sink name returns 404."""
    resp = client.get(
        "/api/v1/datasinks/nonexistent-sink-xyz/datasets",
        headers={"X-Group-ID": "org1/user1"},
    )
    assert resp.status_code == 404


def test_unreachable_datasink_returns_502(client):
    """Unreachable datasink returns 502."""
    with respx.mock:
        respx.get(f"{MOCK_SINK_URL}/datasets").mock(
            side_effect=httpx.ConnectError("unreachable")
        )
        resp = client.get(
            f"/api/v1/datasinks/{MOCK_SINK_NAME}/datasets",
            headers={"X-Group-ID": "org1/user1"},
        )
    assert resp.status_code == 502
