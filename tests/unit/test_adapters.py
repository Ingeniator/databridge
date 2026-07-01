import ast
from pathlib import Path

import pytest


def _read_src(filename: str) -> str:
    return (Path(__file__).parents[2] / "src" / "databridge" / filename).read_text()


def test_get_adapter_returns_correct_class():
    from databridge.adapters import get_adapter, _REGISTRY
    assert "clickhouse" in _REGISTRY
    assert "trino" in _REGISTRY
    assert "langfuse" in _REGISTRY
    assert "s3" in _REGISTRY
    assert "dataset" in _REGISTRY


def test_no_type_branch_in_routes():
    src = _read_src("routes/connections.py")
    assert "conn.type ==" not in src
    assert "connection.type ==" not in src
    assert "from databridge.backends" not in src


def test_no_type_branch_in_adapters():
    src = _read_src("adapters.py")
    assert "conn.type ==" not in src
    assert "connection.type ==" not in src
    assert "from databridge.backends" not in src


def test_duckdb_con_applies_temp_dir(tmp_path):
    from databridge.adapters import S3ConnectionAdapter

    temp_dir = str(tmp_path / "duckdb_spill")
    adapter = S3ConnectionAdapter({}, {})
    con = adapter._duckdb_con({"duckdb_temp_dir": temp_dir})
    try:
        assert con.execute("SELECT current_setting('temp_directory')").fetchone()[0] == temp_dir
    finally:
        con.close()


def test_duckdb_con_defaults_temp_dir(tmp_path):
    from databridge.adapters import S3ConnectionAdapter

    adapter = S3ConnectionAdapter({}, {})
    con = adapter._duckdb_con({})
    try:
        assert con.execute("SELECT current_setting('temp_directory')").fetchone()[0] == "/tmp/duckdb_temp"
    finally:
        con.close()
