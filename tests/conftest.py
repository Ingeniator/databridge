import textwrap
from pathlib import Path

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(scope="session")
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def config_file(tmp_path, fernet_key, monkeypatch) -> Path:
    """Write a minimal config.yaml and point DATABRIDGE_CONFIG at it."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(textwrap.dedent(f"""
        server:
          port: 5010
          debug: false
          silence_probes: false
          hide_auth_inputs: false
        database_url: "postgresql://postgres:postgres@localhost:5432/databridge_test"
        encryption_key: "{fernet_key}"
        datasources: []
    """))
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(cfg))
    yield cfg
