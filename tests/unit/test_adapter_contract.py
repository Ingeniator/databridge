"""Contract tests that run against every BaseAdapter implementation.

Two parametrised fixtures cover the full registry:
  - any_adapter   — all 5 types, tests ping()
  - full_adapter  — all types that implement the ExportableAdapter protocol
                    (preview / count / fetch_page), excludes `dataset` which
                    intentionally raises NotImplementedError for those methods.

Adding a new adapter type: add fixture setup in both fixtures (or just
any_adapter if the type is ping-only) and all contract tests run automatically.
"""
from __future__ import annotations

import pytest
import respx
import httpx
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from databridge.adapters import get_adapter, _REGISTRY

_CH_URL = "http://contract-ch:8123"
_TR_URL = "http://contract-trino:8080"
_LF_URL = "http://contract-langfuse:3000"
_DS_URL = "http://contract-dataset:8020"

_SAMPLE_RECORDS = [{"id": "r1", "value": "x", "ts": "2024-01-01T00:00:00Z"}]
_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
_T1 = datetime(2024, 1, 2, tzinfo=timezone.utc)

# NDJSON line returned by ClickHouse queries
_CH_NDJSON = '{"id":"r1","value":"x"}\n'

# Trino statement response (covers both single-page and nextUri=None cases)
_TR_STMT = {
    "id": "qid",
    "columns": [{"name": "id"}, {"name": "value"}],
    "data": [["r1", "x"]],
    "stats": {"state": "FINISHED"},
}
_TR_INFO = {"starting": False, "nodeVersion": {"version": "400"}}

# Langfuse traces response
_LF_TRACES = {"data": [{"id": "t1", "name": "trace"}], "meta": {"total": 1}}


def _make_s3_mocks():
    """Return (mock_session, asyncio_to_thread_patch) context for S3 adapter."""
    mock_s3 = AsyncMock()
    mock_s3.head_bucket = AsyncMock(return_value={})
    mock_s3.list_objects_v2 = AsyncMock(
        return_value={"Contents": [{"Key": "data.parquet"}], "IsTruncated": False}
    )
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_s3)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.client.return_value = mock_cm
    return mock_session


def _smart_to_thread(fn, *args, **kwargs):
    """Side-effect for asyncio.to_thread: returns int for count, list otherwise."""
    return 42 if getattr(fn, "__name__", "") == "_count" else _SAMPLE_RECORDS


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(params=list(_REGISTRY))
def any_adapter(request):
    """Parametrised over every registered adapter type — used for ping tests."""
    kind = request.param

    if kind == "clickhouse":
        conn = {"type": "clickhouse", "url": _CH_URL}
        creds = {"user": "u", "password": "p", "database": "db", "table": "t"}
        with respx.mock:
            respx.get(f"{_CH_URL}/ping").mock(return_value=httpx.Response(200, text="Ok"))
            yield get_adapter(conn, creds)
        return

    if kind == "trino":
        conn = {"type": "trino", "url": _TR_URL}
        creds = {"user": "u", "catalog": "cat", "schema_name": "sc"}
        with respx.mock:
            respx.get(f"{_TR_URL}/v1/info").mock(return_value=httpx.Response(200, json=_TR_INFO))
            yield get_adapter(conn, creds)
        return

    if kind == "langfuse":
        conn = {"type": "langfuse", "url": _LF_URL}
        creds = {"public_key": "pk", "secret_key": "sk"}
        with respx.mock:
            respx.get(f"{_LF_URL}/api/public/health").mock(return_value=httpx.Response(200))
            yield get_adapter(conn, creds)
        return

    if kind == "dataset":
        conn = {"type": "dataset", "url": _DS_URL}
        creds = {}
        with respx.mock:
            respx.get(f"{_DS_URL}/health").mock(return_value=httpx.Response(200))
            yield get_adapter(conn, creds)
        return

    if kind == "s3":
        conn = {"type": "s3"}
        creds = {"bucket": "b", "endpoint": "http://s3:9000", "access_key_id": "ak",
                 "secret_access_key": "sk", "region": "us-east-1"}
        mock_session = _make_s3_mocks()
        with patch("aioboto3.Session", return_value=mock_session), \
             patch("databridge.adapters.asyncio.to_thread", new=AsyncMock(side_effect=_smart_to_thread)):
            yield get_adapter(conn, creds)
        return


def _ch_handler(request: httpx.Request) -> httpx.Response:
    """Return a count row for COUNT queries, record rows otherwise."""
    if "COUNT" in request.url.params.get("query", ""):
        return httpx.Response(200, text='{"count()":5}\n')
    return httpx.Response(200, text=_CH_NDJSON)


def _tr_handler(request: httpx.Request) -> httpx.Response:
    """Return a scalar row for COUNT queries, record rows otherwise."""
    if b"COUNT" in request.content:
        return httpx.Response(200, json={
            "id": "qid",
            "columns": [{"name": "_col0"}],
            "data": [[5]],
            "stats": {"state": "FINISHED"},
        })
    return httpx.Response(200, json=_TR_STMT)


@pytest.fixture(params=[k for k in _REGISTRY if k != "dataset"])
def full_adapter(request):
    """Parametrised over adapters that implement preview / count / fetch_page."""
    kind = request.param

    if kind == "clickhouse":
        conn = {"type": "clickhouse", "url": _CH_URL}
        creds = {"user": "u", "password": "p", "database": "db", "table": "t"}
        with respx.mock:
            respx.get(f"{_CH_URL}/ping").mock(return_value=httpx.Response(200, text="Ok"))
            respx.get(_CH_URL + "/").mock(side_effect=_ch_handler)
            yield get_adapter(conn, creds)
        return

    if kind == "trino":
        conn = {"type": "trino", "url": _TR_URL}
        creds = {"user": "u", "catalog": "cat", "schema_name": "sc"}
        with respx.mock:
            respx.get(f"{_TR_URL}/v1/info").mock(return_value=httpx.Response(200, json=_TR_INFO))
            respx.post(f"{_TR_URL}/v1/statement").mock(side_effect=_tr_handler)
            yield get_adapter(conn, creds)
        return

    if kind == "langfuse":
        conn = {"type": "langfuse", "url": _LF_URL}
        creds = {"public_key": "pk", "secret_key": "sk"}
        with respx.mock:
            respx.get(f"{_LF_URL}/api/public/health").mock(return_value=httpx.Response(200))
            respx.get(f"{_LF_URL}/api/public/traces").mock(
                return_value=httpx.Response(200, json=_LF_TRACES)
            )
            yield get_adapter(conn, creds)
        return

    if kind == "s3":
        conn = {"type": "s3"}
        creds = {"bucket": "b", "endpoint": "http://s3:9000", "access_key_id": "ak",
                 "secret_access_key": "sk", "region": "us-east-1"}
        mock_session = _make_s3_mocks()
        with patch("aioboto3.Session", return_value=mock_session), \
             patch("databridge.adapters.asyncio.to_thread", new=AsyncMock(side_effect=_smart_to_thread)):
            yield get_adapter(conn, creds)
        return


# ── contract tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_contract_ping(any_adapter):
    await any_adapter.ping()


@pytest.mark.asyncio
async def test_contract_preview_returns_list_of_dicts(full_adapter):
    result = await full_adapter.preview("", _T0, _T1, limit=10)
    assert isinstance(result, list)
    assert all(isinstance(r, dict) for r in result)


@pytest.mark.asyncio
async def test_contract_count_returns_nonnegative_int(full_adapter):
    result = await full_adapter.count("", _T0, _T1)
    assert isinstance(result, int)
    assert result >= 0


@pytest.mark.asyncio
async def test_contract_fetch_page_returns_list_of_dicts(full_adapter):
    result = await full_adapter.fetch_page("", _T0, _T1, limit=10, offset=0)
    assert isinstance(result, list)
    assert all(isinstance(r, dict) for r in result)
