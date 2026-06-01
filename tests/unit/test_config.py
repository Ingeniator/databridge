import os
import textwrap
import pytest
from pathlib import Path


def _write_config(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def _clear_cache():
    from databridge import config as cfg_mod
    cfg_mod.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_cache():
    _clear_cache()
    yield
    _clear_cache()


def test_valid_yaml_loads(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    cfg = _write_config(tmp_path, f"""
        server:
          port: 5010
        database_url: "postgresql://localhost/test"
        encryption_key: "{key}"
        datasources: []
    """)
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(cfg))
    from databridge.config import get_settings
    s = get_settings()
    assert s.server.port == 5010
    assert s.database_url == "postgresql://localhost/test"
    assert s.encryption_key == key


def test_var_expansion(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TEST_DB_PASS", "supersecret")
    monkeypatch.setenv("DATABRIDGE_ENCRYPTION_KEY", key)
    cfg = _write_config(tmp_path, """
        database_url: "postgresql://user:${TEST_DB_PASS}@localhost/db"
        encryption_key: "$DATABRIDGE_ENCRYPTION_KEY"
        datasources: []
    """)
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(cfg))
    from databridge.config import get_settings
    s = get_settings()
    assert "supersecret" in s.database_url
    assert s.encryption_key == key


def test_vault_resolution(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    vault = tmp_path / "vault_secrets"
    vault.write_text(f"MY_KEY={key}\n")
    monkeypatch.setenv("VAULT_SECRETS_PATH", str(vault))
    cfg = _write_config(tmp_path, """
        database_url: "postgresql://localhost/db"
        encryption_key: "vault:MY_KEY"
        datasources: []
    """)
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(cfg))
    from databridge.config import get_settings
    s = get_settings()
    assert s.encryption_key == key


def test_unknown_key_raises(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path, """
        database_url: "postgresql://localhost/db"
        encryption_key: "somekey"
        datasources: []
        unknown_field: "oops"
    """)
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(cfg))
    from databridge.config import get_settings
    with pytest.raises(ValueError, match="unknown_field"):
        get_settings()


def test_missing_config_raises(monkeypatch):
    monkeypatch.setenv("DATABRIDGE_CONFIG", "/nonexistent/config.yaml")
    from databridge.config import get_settings
    with pytest.raises((FileNotFoundError, SystemExit)):
        get_settings()


def test_unresolvable_vault_raises(tmp_path, monkeypatch):
    vault = tmp_path / "vault_secrets"
    vault.write_text("SOME_OTHER_KEY=value\n")
    monkeypatch.setenv("VAULT_SECRETS_PATH", str(vault))
    cfg = _write_config(tmp_path, """
        database_url: "postgresql://localhost/db"
        encryption_key: "vault:MISSING_KEY"
        datasources: []
    """)
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(cfg))
    from databridge.config import get_settings
    with pytest.raises(ValueError, match="MISSING_KEY"):
        get_settings()


def test_singleton(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    cfg = _write_config(tmp_path, f"""
        database_url: "postgresql://localhost/db"
        encryption_key: "{key}"
        datasources: []
    """)
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(cfg))
    from databridge.config import get_settings
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
