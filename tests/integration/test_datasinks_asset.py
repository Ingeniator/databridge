"""Integration test stubs — POST /api/v1/datasinks/{name}/detect-asset-fields."""
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

MOCK_SINK_NAME = "mock-sink"
SRC_NAME = "test-clickhouse"


@pytest.fixture
def config_with_sources_and_sinks(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(textwrap.dedent(f"""
        server:
          port: 5010
          debug: true
          silence_probes: false
        database_url: "postgresql://postgres:postgres@localhost:5432/databridge_test"
        encryption_key: "{key}"
        datasources:
          - name: "{SRC_NAME}"
            type: clickhouse
            url: "http://clickhouse:8123"
            database: default
            table: llogr_events
            user: default
            password: pass
        datasinks:
          - name: "{MOCK_SINK_NAME}"
            type: dataset-mock
            url: "http://dataset-mock:8020"
    """))
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(cfg))
    yield cfg


@pytest.fixture
def client(config_with_sources_and_sinks, monkeypatch):
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


def _mock_adapter_schema():
    return (
        {"image_url": {"type": "string", "example": "https://cdn.example.com/img.png"},
         "name": {"type": "string", "example": "Alice"}},
        5,
    )


def _mock_adapter_preview():
    return [{"image_url": "https://cdn.example.com/img1.png", "name": "Alice"}]


def test_detect_asset_fields_with_system_source(client, monkeypatch):
    """POST with system_source_name returns candidate_fields."""
    import databridge.adapters as adapters_mod

    mock_adapter = MagicMock()
    mock_adapter.schema = AsyncMock(return_value=_mock_adapter_schema())
    mock_adapter.preview = AsyncMock(return_value=_mock_adapter_preview())

    monkeypatch.setattr(adapters_mod, "get_adapter", lambda *a, **kw: mock_adapter)

    resp = client.post(
        f"/api/v1/datasinks/{MOCK_SINK_NAME}/detect-asset-fields",
        json={"system_source_name": SRC_NAME},
        headers={"X-Group-ID": "org1/user1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "candidate_fields" in body
    assert "image_url" in body["candidate_fields"]


def test_detect_asset_fields_no_source_returns_400(client):
    """POST with neither connection_id nor system_source_name → 400."""
    resp = client.post(
        f"/api/v1/datasinks/{MOCK_SINK_NAME}/detect-asset-fields",
        json={},
        headers={"X-Group-ID": "org1/user1"},
    )
    assert resp.status_code == 400


def test_detect_asset_fields_unknown_system_source_returns_404(client):
    """POST with unknown system_source_name → 404."""
    resp = client.post(
        f"/api/v1/datasinks/{MOCK_SINK_NAME}/detect-asset-fields",
        json={"system_source_name": "nonexistent-source"},
        headers={"X-Group-ID": "org1/user1"},
    )
    assert resp.status_code == 404
