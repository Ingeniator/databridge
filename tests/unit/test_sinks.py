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


def _tagme_cfg(url="http://tagme:9000"):
    return DatasinkConfig(
        name="tagme",
        type="tagme-dataset",
        url=url,
        token_url=f"{url}/auth/realms/tagme-public/protocol/openid-connect/token",
        client_id="databridge-service",
        client_secret="s3cr3t",
        audience="tagme",
    )


def _tagme_annotator_cfg(url="http://tagme:9000"):
    return DatasinkConfig(
        name="tagme-annotator",
        type="tagme-annotator",
        url=url,
        token_url=f"{url}/auth/realms/tagme-public/protocol/openid-connect/token",
        client_id="databridge-service",
        client_secret="s3cr3t",
        audience="tagme",
    )


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
        respx.get("http://mock:8020/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        sink = DatasetMockSink(_dataset_cfg())
        await sink.ping()


@pytest.mark.asyncio
async def test_dataset_mock_list_datasets():
    from databridge.sinks.dataset_mock import DatasetMockSink
    with respx.mock:
        respx.get("http://mock:8020/_mock/datasets").mock(
            return_value=httpx.Response(200, json={"datasets": [
                {"id": "id-1", "name": "ds1"},
                {"id": "id-2", "name": "ds2"},
            ], "count": 2})
        )
        sink = DatasetMockSink(_dataset_cfg())
        result = await sink.list_datasets()
    assert result == [{"name": "ds1", "uid": "id-1"}, {"name": "ds2", "uid": "id-2"}]


@pytest.mark.asyncio
async def test_dataset_mock_post_file():
    from databridge.sinks.dataset_mock import DatasetMockSink
    ds_id = "aaaaaaaa-0000-0000-0000-000000000001"
    with respx.mock:
        respx.post("http://mock:8020/realms/test/protocol/openid-connect/token").mock(
            return_value=httpx.Response(200, json={"access_token": "mock-token", "token_type": "Bearer"})
        )
        respx.post("http://mock:8020/api/v0/datasets").mock(
            return_value=httpx.Response(201, json={"id": ds_id, "name": "myds"})
        )
        respx.post(f"http://mock:8020/api/v0/datasets/{ds_id}/files").mock(
            return_value=httpx.Response(201, json={"id": "fid"})
        )
        sink = DatasetMockSink(_dataset_cfg())
        await sink.post_file("myds", {"key": "val"}, "file.json")


@pytest.mark.asyncio
async def test_annotator_mock_list_datasets():
    from databridge.sinks.annotator_mock import AnnotatorMockSink
    with respx.mock:
        respx.get("http://ann:8010/api/v0/markup_project").mock(
            return_value=httpx.Response(200, json={"items": [{"uid": "p1", "name": "proj1"}, {"uid": "p2", "name": "proj2"}], "has_next": False})
        )
        sink = AnnotatorMockSink(_annotator_cfg())
        result = await sink.list_datasets()
    assert result == [{"name": "proj1", "uid": "p1"}, {"name": "proj2", "uid": "p2"}]


@pytest.mark.asyncio
async def test_annotator_mock_full_flow():
    from databridge.sinks.annotator_mock import AnnotatorMockSink
    project_id = "p1111111-0000-0000-0000-000000000001"
    ds_id = "dddddddd-0000-0000-0000-000000000001"
    pool_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    task_id = "tttttttt-0000-0000-0000-000000000001"
    with respx.mock:
        respx.get("http://ann:8010/api/v0/markup_project").mock(
            return_value=httpx.Response(200, json={"items": [{"uid": project_id, "name": "proj1"}], "has_next": False})
        )
        respx.post("http://ann:8010/api/v0/datasets").mock(
            return_value=httpx.Response(201, json={"id": ds_id, "name": "proj1-export"})
        )
        respx.post(f"http://ann:8010/api/v0/datasets/{ds_id}/files").mock(
            return_value=httpx.Response(201, json={"id": "fid"})
        )
        respx.get("http://ann:8010/api/v0/pools/hardcoded").mock(
            return_value=httpx.Response(200, json={"pool_id": pool_id})
        )
        respx.post(f"http://ann:8010/api/v0/markup_project/{project_id}/pools/{pool_id}").mock(
            return_value=httpx.Response(204)
        )
        respx.post("http://ann:8010/api/v0/tasks").mock(
            return_value=httpx.Response(201, json={"uid": task_id})
        )
        respx.post(f"http://ann:8010/api/v0/tasks/{task_id}/start").mock(
            return_value=httpx.Response(200, json={"uid": task_id, "state": "RUNNING"})
        )
        sink = AnnotatorMockSink(_annotator_cfg())
        await sink.create_dataset("proj1")
        await sink.post_file("proj1", {"data": "x"}, "record.json")
        await sink.finalise()
    assert sink.external_id == task_id


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


# ── TagmeDatasetSink: Keycloak token exchange ───────────────────────────────────────

def _tagme_token_handler(subject_token="svc-token", expires_in=300, expect_org=None, expect_user=None):
    """Build a respx side_effect that fakes a Keycloak token endpoint serving
    both the client_credentials grant (service token) and the token-exchange
    grant (service token -> user-asserting token), so tests can assert the
    exchange request carries the right actor claims without a real Keycloak.
    """
    from urllib.parse import parse_qsl

    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(parse_qsl(request.content.decode()))
        if body["grant_type"] == "client_credentials":
            assert body["client_id"] == "databridge-service"
            assert body["client_secret"] == "s3cr3t"
            return httpx.Response(200, json={"access_token": subject_token, "expires_in": 60})
        assert body["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
        assert body["subject_token"] == subject_token
        assert body["subject_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
        assert body["audience"] == "tagme"
        if expect_user is not None:
            assert body["requested_subject"] == expect_user
        if expect_org is not None:
            assert request.headers.get("Organization-Id") == expect_org
        return httpx.Response(200, json={"access_token": "exchanged-token", "expires_in": expires_in})

    return handler


@pytest.mark.asyncio
async def test_tagme_requires_set_actor_before_use():
    from databridge.sinks.tagme import TagmeDatasetSink
    sink = TagmeDatasetSink(_tagme_cfg())
    with pytest.raises(RuntimeError, match="set_actor"):
        await sink.list_datasets()


@pytest.mark.asyncio
async def test_tagme_token_exchange_carries_actor_claims():
    from databridge.sinks.tagme import TagmeDatasetSink
    cfg = _tagme_cfg()
    with respx.mock:
        respx.post(cfg.token_url).mock(
            side_effect=_tagme_token_handler(expect_org="org-1", expect_user="user-1")
        )
        respx.get(f"{cfg.url}/api/v0/datasets").mock(
            return_value=httpx.Response(200, json={"items": [], "has_next": False})
        )
        sink = TagmeDatasetSink(cfg)
        sink.set_actor("org-1", "user-1")
        result = await sink.list_datasets()
    assert result == []


@pytest.mark.asyncio
async def test_tagme_create_dataset_reuses_existing_by_name():
    from databridge.sinks.tagme import TagmeDatasetSink
    cfg = _tagme_cfg()
    ds_id = "aaaaaaaa-0000-0000-0000-000000000001"
    with respx.mock:
        respx.post(cfg.token_url).mock(side_effect=_tagme_token_handler())
        respx.get(f"{cfg.url}/api/v0/datasets").mock(
            return_value=httpx.Response(200, json={"items": [{"id": ds_id, "name": "myds"}], "has_next": False})
        )
        create_route = respx.post(f"{cfg.url}/api/v0/datasets")
        sink = TagmeDatasetSink(cfg)
        sink.set_actor("org-1", "user-1")
        await sink.create_dataset("myds")
    assert sink.external_id == ds_id
    assert not create_route.called


@pytest.mark.asyncio
async def test_tagme_create_dataset_posts_when_absent():
    from databridge.sinks.tagme import TagmeDatasetSink
    cfg = _tagme_cfg()
    ds_id = "aaaaaaaa-0000-0000-0000-000000000002"
    with respx.mock:
        respx.post(cfg.token_url).mock(side_effect=_tagme_token_handler())
        respx.get(f"{cfg.url}/api/v0/datasets").mock(
            return_value=httpx.Response(200, json={"items": [], "has_next": False})
        )
        create_route = respx.post(f"{cfg.url}/api/v0/datasets").mock(
            return_value=httpx.Response(201, json={"id": ds_id, "name": "myds", "access": "organization"})
        )
        sink = TagmeDatasetSink(cfg)
        sink.set_actor("org-1", "user-1")
        await sink.create_dataset("myds")
    assert sink.external_id == ds_id
    assert create_route.called
    sent = json.loads(create_route.calls.last.request.content)
    assert sent == {"name": "myds", "access": "organization"}


@pytest.mark.asyncio
async def test_tagme_post_file_uploads_multipart():
    from databridge.sinks.tagme import TagmeDatasetSink
    cfg = _tagme_cfg()
    ds_id = "aaaaaaaa-0000-0000-0000-000000000003"
    with respx.mock:
        respx.post(cfg.token_url).mock(side_effect=_tagme_token_handler())
        respx.get(f"{cfg.url}/api/v0/datasets").mock(
            return_value=httpx.Response(200, json={"items": [{"id": ds_id, "name": "myds"}], "has_next": False})
        )
        respx.post(f"{cfg.url}/api/v0/datasets/{ds_id}/files").mock(
            return_value=httpx.Response(200, json={"created_files": [{"uid": "fid", "filename": "file.json"}], "errors": []})
        )
        sink = TagmeDatasetSink(cfg)
        sink.set_actor("org-1", "user-1")
        ref = await sink.post_file("myds", {"key": "val", "source_url": "http://x/f"}, "file.json")
    assert ref == "http://x/f"


@pytest.mark.asyncio
async def test_tagme_reexchanges_token_when_actor_changes():
    """A cached token for one user must not leak to a sink reused for another."""
    from databridge.sinks.tagme import TagmeDatasetSink
    cfg = _tagme_cfg()
    with respx.mock:
        route = respx.post(cfg.token_url).mock(side_effect=_tagme_token_handler())
        respx.get(f"{cfg.url}/api/v0/datasets").mock(
            return_value=httpx.Response(200, json={"items": [], "has_next": False})
        )
        sink = TagmeDatasetSink(cfg)
        sink.set_actor("org-1", "user-1")
        await sink.list_datasets()
        calls_after_first = route.call_count
        sink.set_actor("org-2", "user-2")
        await sink.list_datasets()
    # each actor switch re-runs client_credentials + token-exchange (2 calls)
    assert route.call_count == calls_after_first + 2


# ── TagmeAnnotatorSink: upload into one task in an existing project ─────────

@pytest.mark.asyncio
async def test_tagme_annotator_create_dataset_rejects_unknown_project():
    from databridge.sinks.tagme import TagmeAnnotatorSink
    cfg = _tagme_annotator_cfg()
    with respx.mock:
        respx.post(cfg.token_url).mock(side_effect=_tagme_token_handler())
        respx.get(f"{cfg.url}/api/v0/markup_project").mock(
            return_value=httpx.Response(200, json={"items": [], "has_next": False})
        )
        sink = TagmeAnnotatorSink(cfg)
        sink.set_actor("org-1", "user-1")
        with pytest.raises(RuntimeError, match="not found"):
            await sink.create_dataset("no-such-project")


@pytest.mark.asyncio
async def test_tagme_annotator_opens_task_in_existing_project_by_name():
    from databridge.sinks.tagme import TagmeAnnotatorSink
    cfg = _tagme_annotator_cfg()
    project_id = "pppppppp-0000-0000-0000-000000000001"
    task_id = "tttttttt-0000-0000-0000-000000000001"
    with respx.mock:
        respx.post(cfg.token_url).mock(side_effect=_tagme_token_handler())
        respx.get(f"{cfg.url}/api/v0/markup_project").mock(
            return_value=httpx.Response(200, json={"items": [{"uid": project_id, "name": "proj1"}], "has_next": False})
        )
        create_task_route = respx.post(f"{cfg.url}/api/v0/tasks").mock(
            return_value=httpx.Response(201, json={"uid": task_id})
        )
        sink = TagmeAnnotatorSink(cfg)
        sink.set_actor("org-1", "user-1")
        await sink.create_dataset("proj1")
    assert sink.external_id == task_id
    sent = json.loads(create_task_route.calls.last.request.content)
    assert sent == {"project_id": project_id}


@pytest.mark.asyncio
async def test_tagme_annotator_post_file_requires_create_dataset_first():
    from databridge.sinks.tagme import TagmeAnnotatorSink
    sink = TagmeAnnotatorSink(_tagme_annotator_cfg())
    with pytest.raises(RuntimeError, match="create_dataset"):
        await sink.post_file("proj1", {"x": 1})


@pytest.mark.asyncio
async def test_tagme_annotator_full_flow_buffers_then_writes_payload_once():
    from databridge.sinks.tagme import TagmeAnnotatorSink
    cfg = _tagme_annotator_cfg()
    project_id = "pppppppp-0000-0000-0000-000000000002"
    task_id = "tttttttt-0000-0000-0000-000000000002"
    with respx.mock:
        respx.post(cfg.token_url).mock(side_effect=_tagme_token_handler())
        respx.get(f"{cfg.url}/api/v0/markup_project").mock(
            return_value=httpx.Response(200, json={"items": [{"uid": project_id, "name": "proj1"}], "has_next": False})
        )
        respx.post(f"{cfg.url}/api/v0/tasks").mock(
            return_value=httpx.Response(201, json={"uid": task_id})
        )
        payload_route = respx.put(f"{cfg.url}/api/v0/tasks/{task_id}/payload").mock(
            return_value=httpx.Response(200, json={"payload": {}})
        )
        start_route = respx.post(f"{cfg.url}/api/v0/tasks/{task_id}/start").mock(
            return_value=httpx.Response(200, json={"uid": task_id})
        )
        sink = TagmeAnnotatorSink(cfg)
        sink.set_actor("org-1", "user-1")
        await sink.create_dataset("proj1")
        ref1 = await sink.post_file("proj1", {"id": 1, "source_url": "http://x/1"})
        ref2 = await sink.post_file("proj1", {"id": 2, "source_url": "http://x/2"})
        assert not payload_route.called  # buffered, not written per-record
        await sink.finalise()
    assert ref1 == "http://x/1"
    assert ref2 == "http://x/2"
    assert payload_route.called
    sent = json.loads(payload_route.calls.last.request.content)
    assert sent == {"payload": {"entities": [{"id": 1, "source_url": "http://x/1"}, {"id": 2, "source_url": "http://x/2"}]}}
    assert start_route.called
