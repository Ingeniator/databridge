"""Contract tests that run against every BaseSink implementation.

Each test exercises a single method or the full lifecycle.  The fixture
parametrises over all four sink types and is responsible for wiring up HTTP
mocks (respx) for the service-backed sinks and a tmp directory for the local
ones, so the test bodies stay sink-agnostic.
"""
from __future__ import annotations

import pytest
import respx
import httpx

from databridge.config import DatasinkConfig

# ── stable IDs used across HTTP mock routes ─────────────────────────────────
_DS_URL = "http://contract-ds:8020"
_ANN_URL = "http://contract-ann:8010"
_DS_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_PROJECT_ID = "pppppppp-0000-0000-0000-000000000001"
_POOL_ID = "b1b2b3b4-c5c6-7890-abcd-ef1234567890"
_TASK_ID = "tttttttt-0000-0000-0000-000000000001"


@pytest.fixture(params=["dataset-mock", "annotator-mock", "local-zip", "local-jsonl"])
def contract_sink(request, tmp_path):
    sink_type = request.param

    if sink_type == "local-zip":
        from databridge.sinks.local_zip import LocalZipSink
        yield LocalZipSink(DatasinkConfig(name="zip", type="local-zip", path=str(tmp_path)))
        return

    if sink_type == "local-jsonl":
        from databridge.sinks.local_jsonl import LocalJsonlSink
        yield LocalJsonlSink(DatasinkConfig(name="jsonl", type="local-jsonl", path=str(tmp_path)))
        return

    if sink_type == "dataset-mock":
        from databridge.sinks.dataset_mock import DatasetMockSink
        with respx.mock:
            respx.get(f"{_DS_URL}/health").mock(return_value=httpx.Response(200))
            respx.get(f"{_DS_URL}/_mock/datasets").mock(
                return_value=httpx.Response(200, json={"datasets": [{"name": "existing"}]})
            )
            respx.post(f"{_DS_URL}/realms/test/protocol/openid-connect/token").mock(
                return_value=httpx.Response(200, json={"access_token": "tok", "token_type": "Bearer"})
            )
            respx.post(f"{_DS_URL}/api/v0/datasets").mock(
                return_value=httpx.Response(201, json={"id": _DS_ID, "name": "testds"})
            )
            respx.post(f"{_DS_URL}/api/v0/datasets/{_DS_ID}/files").mock(
                return_value=httpx.Response(201, json={"id": "fid"})
            )
            yield DatasetMockSink(DatasinkConfig(name="ds", type="dataset-mock", url=_DS_URL))
        return

    if sink_type == "annotator-mock":
        from databridge.sinks.annotator_mock import AnnotatorMockSink
        with respx.mock:
            respx.get(f"{_ANN_URL}/health").mock(return_value=httpx.Response(200))
            # called by both list_datasets and create_dataset
            respx.get(f"{_ANN_URL}/api/v0/markup_project").mock(
                return_value=httpx.Response(
                    200,
                    json={"items": [{"uid": _PROJECT_ID, "name": "testds"}], "has_next": False},
                )
            )
            respx.post(f"{_ANN_URL}/api/v0/datasets").mock(
                return_value=httpx.Response(201, json={"id": _DS_ID, "name": "testds"})
            )
            respx.post(f"{_ANN_URL}/api/v0/datasets/{_DS_ID}/files").mock(
                return_value=httpx.Response(201, json={"id": "fid"})
            )
            respx.get(f"{_ANN_URL}/api/v0/pools/hardcoded").mock(
                return_value=httpx.Response(200, json={"pool_id": _POOL_ID})
            )
            respx.post(f"{_ANN_URL}/api/v0/markup_project/{_PROJECT_ID}/pools/{_POOL_ID}").mock(
                return_value=httpx.Response(204)
            )
            respx.post(f"{_ANN_URL}/api/v0/tasks").mock(
                return_value=httpx.Response(201, json={"uid": _TASK_ID})
            )
            respx.post(f"{_ANN_URL}/api/v0/tasks/{_TASK_ID}/start").mock(
                return_value=httpx.Response(200, json={"uid": _TASK_ID, "state": "RUNNING"})
            )
            yield AnnotatorMockSink(DatasinkConfig(name="ann", type="annotator-mock", url=_ANN_URL))
        return


# ── contract tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_contract_ping(contract_sink):
    await contract_sink.ping()


@pytest.mark.asyncio
async def test_contract_list_datasets_returns_dicts(contract_sink):
    result = await contract_sink.list_datasets()
    assert isinstance(result, list)
    assert all(isinstance(d, dict) and "name" in d and "uid" in d for d in result)


@pytest.mark.asyncio
async def test_contract_full_lifecycle(contract_sink):
    """create_dataset → post_file → finalise; post_file returns str; external_id is str|None."""
    await contract_sink.create_dataset("testds")
    ref = await contract_sink.post_file("testds", {"source_url": "http://x/img.jpg"}, "rec.json")
    assert isinstance(ref, str)
    await contract_sink.finalise()
    assert contract_sink.external_id is None or isinstance(contract_sink.external_id, str)
