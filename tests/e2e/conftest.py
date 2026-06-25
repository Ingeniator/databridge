import pytest
import requests

_BASE = "http://localhost:5010"
_AUTH = {"X-Group-ID": "e2e-user"}
_S3_ENDPOINT = "http://localhost:9200"
_S3_BUCKET = "test-media"
_CH_CONN = {
    "label": "E2E ClickHouse",
    "type": "clickhouse",
    "role": "source",
    "connection_url": "http://localhost:8123",
    "credentials": {"user": "default", "password": ""},
}


@pytest.fixture
def browser_context_args(browser_context_args):
    """Inject X-Group-ID into every browser request (including JS fetch calls)."""
    return {**browser_context_args, "extra_http_headers": {"X-Group-ID": "e2e-user"}}


@pytest.fixture(scope="session")
def s3_media_base_url():
    """Base URL for the MinIO test-media bucket (host-accessible via docker-compose.dev.yml)."""
    return f"{_S3_ENDPOINT}/{_S3_BUCKET}"


@pytest.fixture
def api_conn():
    """Create a ClickHouse connection via API; delete it after the test."""
    r = requests.post(f"{_BASE}/api/v1/connections", json=_CH_CONN, headers=_AUTH, timeout=5)
    r.raise_for_status()
    conn_id = r.json()["id"]
    yield conn_id
    requests.delete(f"{_BASE}/api/v1/connections/{conn_id}", headers=_AUTH, timeout=5)
