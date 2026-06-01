"""T024c — system source integration tests (must be committed before T024b)."""
import textwrap
from uuid import uuid5, NAMESPACE_DNS

import pytest
import respx
import httpx
from cryptography.fernet import Fernet
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


CH_SOURCE_NAME = "prod-clickhouse"
CH_SOURCE_URL = "http://clickhouse:8123"


@pytest.fixture
def config_with_sources(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(textwrap.dedent(f"""
        server:
          port: 5010
          debug: false
          silence_probes: false
          hide_auth_inputs: false
        database_url: "postgresql://postgres:postgres@localhost:5432/databridge_test"
        encryption_key: "{key}"
        datasources:
          - name: "{CH_SOURCE_NAME}"
            type: clickhouse
            url: "{CH_SOURCE_URL}"
            database: "default"
            table: "llogr_events"
            user: "default"
            password: "s3cr3t"
    """))
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(cfg))
    yield cfg, key


@pytest.fixture
def client(config_with_sources, monkeypatch):
    cfg, _ = config_with_sources
    from databridge.config import get_settings
    get_settings.cache_clear()
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock()
    pool.close = AsyncMock()
    with patch("databridge.main.create_pool", AsyncMock(return_value=pool)):
        from databridge.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    get_settings.cache_clear()


def test_list_includes_system_sources(client):
    r = client.get("/api/v1/connections", headers={"X-Group-ID": "u1"})
    assert r.status_code == 200
    items = r.json()["items"]
    sys_items = [i for i in items if i["system"] is True]
    assert len(sys_items) == 1
    assert sys_items[0]["label"] == CH_SOURCE_NAME
    assert sys_items[0]["type"] == "clickhouse"
    assert sys_items[0]["connection_url"] == CH_SOURCE_URL


def test_system_source_id_is_deterministic(client):
    expected_id = str(uuid5(NAMESPACE_DNS, CH_SOURCE_NAME))
    r = client.get("/api/v1/connections", headers={"X-Group-ID": "u1"})
    items = r.json()["items"]
    sys_items = [i for i in items if i["system"] is True]
    assert sys_items[0]["id"] == expected_id


def test_patch_system_source_returns_404(client):
    sys_id = str(uuid5(NAMESPACE_DNS, CH_SOURCE_NAME))
    r = client.patch(
        f"/api/v1/connections/{sys_id}",
        json={"label": "renamed"},
        headers={"X-Group-ID": "u1"},
    )
    assert r.status_code == 404


def test_delete_system_source_returns_404(client):
    sys_id = str(uuid5(NAMESPACE_DNS, CH_SOURCE_NAME))
    r = client.delete(f"/api/v1/connections/{sys_id}", headers={"X-Group-ID": "u1"})
    assert r.status_code == 404


def test_ping_system_source(client):
    sys_id = str(uuid5(NAMESPACE_DNS, CH_SOURCE_NAME))
    with respx.mock:
        respx.get(f"{CH_SOURCE_URL}/ping").mock(return_value=httpx.Response(200))
        r = client.post(
            f"/api/v1/connections/{sys_id}/ping",
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "reachable"


def test_preview_system_source(client):
    sys_id = str(uuid5(NAMESPACE_DNS, CH_SOURCE_NAME))
    with respx.mock:
        respx.get(CH_SOURCE_URL + "/").mock(
            return_value=httpx.Response(200, text='{"id":"1","msg":"hello"}\n')
        )
        r = client.post(
            f"/api/v1/connections/{sys_id}/preview",
            json={"limit": 5},
            headers={"X-Group-ID": "u1"},
        )
    assert r.status_code == 200
    body = r.json()
    assert "results" in body
    assert body["connection_id"] == sys_id
