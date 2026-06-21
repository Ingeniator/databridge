import pytest
import structlog.testing
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def _no_debug_mode():
    mock_settings = MagicMock()
    mock_settings.server.debug = False
    with patch("databridge.auth.get_settings", return_value=mock_settings):
        yield


def _make_app():
    from databridge.auth import get_auth
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(auth=__import__("fastapi").Depends(get_auth)):
        return {"public_key": auth.public_key}

    return app


def test_x_group_id_header():
    client = TestClient(_make_app(), raise_server_exceptions=True)
    r = client.get("/whoami", headers={"X-Group-ID": "tenant/user"})
    assert r.status_code == 200
    assert r.json()["public_key"] == "tenant/user"


def test_basic_auth_fallback():
    import base64
    client = TestClient(_make_app(), raise_server_exceptions=True)
    creds = base64.b64encode(b"mykey:mysecret").decode()
    r = client.get("/whoami", headers={"Authorization": f"Basic {creds}"})
    assert r.status_code == 200
    assert r.json()["public_key"] == "mykey"


def test_path_traversal_stripped():
    client = TestClient(_make_app(), raise_server_exceptions=True)
    r = client.get("/whoami", headers={"X-Group-ID": "../etc/passwd"})
    assert r.status_code == 401


def test_empty_key_returns_401():
    client = TestClient(_make_app(), raise_server_exceptions=True)
    r = client.get("/whoami", headers={"X-Group-ID": ""})
    assert r.status_code == 401


def test_no_auth_returns_401():
    client = TestClient(_make_app(), raise_server_exceptions=True)
    r = client.get("/whoami")
    assert r.status_code == 401


def test_authenticated_audit_event_emitted():
    with structlog.testing.capture_logs() as logs:
        client = TestClient(_make_app(), raise_server_exceptions=True)
        r = client.get("/whoami", headers={"X-Group-ID": "user1"})
    assert r.status_code == 200
    events = [l["event"] for l in logs]
    assert "authenticated" in events


def test_auth_rejected_audit_event_emitted():
    with structlog.testing.capture_logs() as logs:
        client = TestClient(_make_app(), raise_server_exceptions=True)
        r = client.get("/whoami")
    assert r.status_code == 401
    events = [l["event"] for l in logs]
    assert "auth_rejected" in events
    rejected = next(l for l in logs if l["event"] == "auth_rejected")
    assert "reason" in rejected
    assert "path" in rejected
