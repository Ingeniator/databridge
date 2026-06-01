"""T063 — assert no conn.type == branches in routes/connections.py or adapters.py."""
from pathlib import Path


def _src(filename: str) -> str:
    return (Path(__file__).parents[2] / "src" / "databridge" / filename).read_text()


def test_no_type_branch_in_routes():
    src = _src("routes/connections.py")
    assert "conn.type ==" not in src, "conn.type == branch found in routes/connections.py"
    assert "connection.type ==" not in src, "connection.type == branch found"
    assert "from databridge.backends" not in src, "direct backend import bypasses registry"


def test_no_type_branch_in_adapters():
    src = _src("adapters.py")
    assert "conn.type ==" not in src, "conn.type == branch found in adapters.py"
    assert "connection.type ==" not in src, "connection.type == branch found"
    assert "from databridge.backends" not in src, "direct backend import bypasses registry"


def test_s3_duckdb_uses_to_thread():
    src = _src("adapters.py")
    # S3ConnectionAdapter.preview uses asyncio.to_thread for DuckDB (blocking)
    assert "asyncio.to_thread" in src, "DuckDB/S3 blocking calls must use asyncio.to_thread"
