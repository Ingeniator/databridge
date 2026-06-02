"""Failing unit test stubs — sink implementations and registry."""
import pytest
import pytest_asyncio
import respx
import httpx
import json
import zipfile
import io

from databridge.config import DatasinkConfig


def _dataset_cfg(url="http://mock:8020"):
    return DatasinkConfig(name="mock", type="dataset-mock", url=url)


def _annotator_cfg(url="http://ann:8010"):
    return DatasinkConfig(name="ann", type="annotator-mock", url=url)


def _local_zip_cfg(tmp_path, template=""):
    return DatasinkConfig(name="zp", type="local-zip", path=str(tmp_path), filename_template=template)


def _local_jsonl_cfg(tmp_path):
    return DatasinkConfig(name="jl", type="local-jsonl", path=str(tmp_path))


@pytest.mark.asyncio
async def test_base_sink_is_abstract():
    """BaseSink cannot be instantiated directly."""
    from databridge.sinks.base import BaseSink
    with pytest.raises(TypeError):
        BaseSink(_dataset_cfg())


@pytest.mark.asyncio
async def test_dataset_mock_ping():
    from databridge.sinks.dataset_mock import DatasetMockSink
    with respx.mock:
        respx.get("http://mock:8020/health").mock(return_value=httpx.Response(200))
        sink = DatasetMockSink(_dataset_cfg())
        await sink.ping()


@pytest.mark.asyncio
async def test_dataset_mock_list_datasets():
    from databridge.sinks.dataset_mock import DatasetMockSink
    with respx.mock:
        respx.get("http://mock:8020/datasets").mock(
            return_value=httpx.Response(200, json={"datasets": ["ds1", "ds2"]})
        )
        sink = DatasetMockSink(_dataset_cfg())
        result = await sink.list_datasets()
    assert result == ["ds1", "ds2"]


@pytest.mark.asyncio
async def test_dataset_mock_post_file():
    from databridge.sinks.dataset_mock import DatasetMockSink
    with respx.mock:
        respx.post("http://mock:8020/datasets/myds/files").mock(return_value=httpx.Response(201))
        sink = DatasetMockSink(_dataset_cfg())
        await sink.post_file("myds", {"key": "val"}, "file.json")


@pytest.mark.asyncio
async def test_annotator_mock_list_datasets():
    from databridge.sinks.annotator_mock import AnnotatorMockSink
    with respx.mock:
        respx.get("http://ann:8010/api/v1/projects").mock(
            return_value=httpx.Response(200, json=[{"name": "proj1"}, {"name": "proj2"}])
        )
        sink = AnnotatorMockSink(_annotator_cfg())
        result = await sink.list_datasets()
    assert result == ["proj1", "proj2"]


@pytest.mark.asyncio
async def test_annotator_mock_post_file():
    from databridge.sinks.annotator_mock import AnnotatorMockSink
    with respx.mock:
        respx.post("http://ann:8010/api/v1/projects/proj1/tasks").mock(
            return_value=httpx.Response(201)
        )
        sink = AnnotatorMockSink(_annotator_cfg())
        await sink.post_file("proj1", {"data": "x"}, "task.json")


@pytest.mark.asyncio
async def test_local_zip_post_file_writes_json(tmp_path):
    from databridge.sinks.local_zip import LocalZipSink
    cfg = _local_zip_cfg(tmp_path)
    sink = LocalZipSink(cfg)
    await sink.create_dataset("testds")
    await sink.post_file("testds", {"id": "abc"}, "abc.json")
    await sink.finalise()
    # verify zip was written
    zips = list(tmp_path.glob("*.zip"))
    assert len(zips) == 1
    with zipfile.ZipFile(zips[0]) as zf:
        names = zf.namelist()
        assert len(names) == 1
        content = json.loads(zf.read(names[0]))
        assert content["id"] == "abc"


@pytest.mark.asyncio
async def test_local_zip_filename_hash_fallback(tmp_path):
    """When filename_template is empty, filename is content hash."""
    from databridge.sinks.local_zip import LocalZipSink
    import hashlib
    cfg = _local_zip_cfg(tmp_path, template="")
    sink = LocalZipSink(cfg)
    record = {"x": 1}
    expected_hash = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:16]
    await sink.create_dataset("ds")
    await sink.post_file("ds", record)
    await sink.finalise()
    zips = list(tmp_path.glob("*.zip"))
    with zipfile.ZipFile(zips[0]) as zf:
        assert expected_hash in zf.namelist()[0]


@pytest.mark.asyncio
async def test_local_jsonl_skips_non_serializable(tmp_path):
    from databridge.sinks.local_jsonl import LocalJsonlSink
    cfg = _local_jsonl_cfg(tmp_path)
    sink = LocalJsonlSink(cfg)
    await sink.create_dataset("ds")
    # non-serializable object
    await sink.post_file("ds", {"ok": True}, "a.json")
    await sink.post_file("ds", {"bad": object()}, "b.json")
    await sink.finalise()
    assert sink.records_skipped == 1
    jsonl_files = list(tmp_path.glob("*.jsonl"))
    lines = jsonl_files[0].read_text().strip().splitlines()
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_get_sink_raises_for_unknown_type():
    from databridge.sinks import get_sink
    cfg = DatasinkConfig(name="x", type="unknown-type", url="http://x")
    with pytest.raises(ValueError, match="Unknown datasink type"):
        get_sink(cfg)
