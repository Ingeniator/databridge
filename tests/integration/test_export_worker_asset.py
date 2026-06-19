"""Worker integration tests — asset resolution path in run_export_job."""
from __future__ import annotations

import json
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from uuid import uuid4

from databridge.config import (
    DatasinkConfig,
    ExportSettings,
    Settings,
    ServerConfig,
    SystemSourceConfig,
)
from databridge.export.worker import run_export_job

S3_URL_A = "https://bucket.s3.amazonaws.com/media/clip.mp4"
S3_URL_B = "https://bucket.s3.amazonaws.com/media/intro.mp4"
ASSET_BYTES = b"\x00\x01\x02\x03"


# ── pool / settings helpers ───────────────────────────────────────────────────

def _job(**overrides) -> dict:
    now = datetime.now(timezone.utc)
    base = {
        "id": uuid4(),
        "org_id": "test-org",
        "user_id": "test-user",
        "datasource_type": "system",
        "datasource_ref": "fake-source",
        "datasource_filter": "{}",
        "datasink_name": "main-sink",
        "destination_dataset": "export_ds",
        "asset_resolution": True,
        "asset_url_fields": json.dumps(["media_url"]),
        "asset_url_prefix": "",
        "asset_datasink_name": "asset-sink",
        "asset_dataset": "asset_ds",
        "status": "pending",
        "records_total": None,
        "records_processed": 0,
        "records_skipped": 0,
        "asset_errors": 0,
        "error_message": None,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "last_heartbeat_at": None,
        "masking_rules": None,
        "sampling_config": None,
        "webhook_url": None,
        "webhook_enabled": False,
        "webhook_payload_template": None,
    }
    base.update(overrides)
    return base


class _Pool:
    def __init__(self, job: dict):
        self._job = job

    async def fetchrow(self, *args):
        return self._job

    async def fetchval(self, *args):
        return False  # is_job_cancelled → not cancelled

    async def execute(self, *args):
        return "OK"

    async def close(self):
        pass


def _settings() -> Settings:
    return Settings(
        server=ServerConfig(debug=True),
        database_url="postgresql://localhost/test",
        encryption_key="Ks_7gv0quQuNMvwLHNhToPPgrQw7Z3Zjm2r-4mTJqF4=",
        datasources=(
            SystemSourceConfig(name="fake-source", type="clickhouse", url="http://fake:8123"),
        ),
        datasinks=(
            DatasinkConfig(name="main-sink", type="local-jsonl", path="/tmp"),
            DatasinkConfig(name="asset-sink", type="local-jsonl", path="/tmp"),
        ),
        export=ExportSettings(batch_size=10),
    )


def _mock_adapter(records: list[dict]) -> MagicMock:
    a = MagicMock()
    a.count = AsyncMock(return_value=len(records))
    a.fetch_page = AsyncMock(return_value=records)
    return a


def _mock_sink() -> MagicMock:
    s = MagicMock()
    s.ping = AsyncMock()
    s.create_dataset = AsyncMock()
    s.post_file = AsyncMock(side_effect=lambda dataset, record, filename=None: f"{dataset}/{filename}" if filename else "")
    s.finalise = AsyncMock()
    return s


def _sink_factory(main_sink, asset_sink):
    def _get(cfg):
        return main_sink if cfg.name == "main-sink" else asset_sink
    return _get


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_worker_resolves_media_url():
    """Records with media_url have the URL downloaded and replaced with the filename."""
    records = [
        {"id": "1", "title": "Clip A", "media_url": S3_URL_A},
        {"id": "2", "title": "Clip B", "media_url": S3_URL_B},
    ]
    job = _job()
    main_sink = _mock_sink()
    asset_sink = _mock_sink()

    with (
        patch("databridge.adapters.get_adapter", return_value=_mock_adapter(records)),
        patch("databridge.sinks.get_sink", side_effect=_sink_factory(main_sink, asset_sink)),
        respx.mock,
    ):
        respx.get(S3_URL_A).mock(return_value=httpx.Response(200, content=ASSET_BYTES))
        respx.get(S3_URL_B).mock(return_value=httpx.Response(200, content=ASSET_BYTES))
        await run_export_job({"pool": _Pool(job), "settings": _settings()}, str(job["id"]))

    # Both records posted to main sink
    assert main_sink.post_file.call_count == 2
    posted = [c.args[1] for c in main_sink.post_file.call_args_list]
    # media_url replaced with asset_dataset/filename path, not the original S3 URL
    assert {r["media_url"] for r in posted} == {"asset_ds/clip.mp4", "asset_ds/intro.mp4"}

    # Asset content posted to asset sink for each record
    assert asset_sink.post_file.call_count == 2
    asset_payloads = [c.args[1] for c in asset_sink.post_file.call_args_list]
    for payload in asset_payloads:
        assert payload["data"] == ASSET_BYTES.hex()
        assert payload["source_url"] in (S3_URL_A, S3_URL_B)

    asset_sink.finalise.assert_called_once()


@pytest.mark.asyncio
async def test_worker_asset_resolution_404_skips_record():
    """A 404 on the asset URL causes that record to be skipped; job still completes."""
    records = [
        {"id": "1", "media_url": S3_URL_A},   # will 404
        {"id": "2", "media_url": S3_URL_B},   # will 200
    ]
    job = _job()
    main_sink = _mock_sink()
    asset_sink = _mock_sink()

    with (
        patch("databridge.adapters.get_adapter", return_value=_mock_adapter(records)),
        patch("databridge.sinks.get_sink", side_effect=_sink_factory(main_sink, asset_sink)),
        respx.mock,
    ):
        respx.get(S3_URL_A).mock(return_value=httpx.Response(404))
        respx.get(S3_URL_B).mock(return_value=httpx.Response(200, content=ASSET_BYTES))
        await run_export_job({"pool": _Pool(job), "settings": _settings()}, str(job["id"]))

    # Only the successful record reaches the main sink
    assert main_sink.post_file.call_count == 1
    assert main_sink.post_file.call_args.args[1]["id"] == "2"

    # Only one asset was stored
    assert asset_sink.post_file.call_count == 1


@pytest.mark.asyncio
async def test_worker_asset_resolution_network_error_skips_record():
    """A network error during asset fetch causes that record to be skipped."""
    records = [{"id": "1", "media_url": S3_URL_A}]
    job = _job()
    main_sink = _mock_sink()
    asset_sink = _mock_sink()

    with (
        patch("databridge.adapters.get_adapter", return_value=_mock_adapter(records)),
        patch("databridge.sinks.get_sink", side_effect=_sink_factory(main_sink, asset_sink)),
        respx.mock,
    ):
        respx.get(S3_URL_A).mock(side_effect=httpx.ConnectError("refused"))
        await run_export_job({"pool": _Pool(job), "settings": _settings()}, str(job["id"]))

    main_sink.post_file.assert_not_called()
    asset_sink.post_file.assert_not_called()


@pytest.mark.asyncio
async def test_worker_asset_resolution_with_url_prefix():
    """url_prefix is prepended before fetching; stored asset filename comes from the path."""
    prefix = "https://cdn.example.com/"
    partial = "photos/event.jpg"
    full_url = prefix + partial

    records = [{"id": "1", "media_url": partial}]
    job = _job(asset_url_prefix=prefix)
    main_sink = _mock_sink()
    asset_sink = _mock_sink()

    with (
        patch("databridge.adapters.get_adapter", return_value=_mock_adapter(records)),
        patch("databridge.sinks.get_sink", side_effect=_sink_factory(main_sink, asset_sink)),
        respx.mock,
    ):
        respx.get(full_url).mock(return_value=httpx.Response(200, content=ASSET_BYTES))
        await run_export_job({"pool": _Pool(job), "settings": _settings()}, str(job["id"]))

    assert main_sink.post_file.call_count == 1
    posted_record = main_sink.post_file.call_args.args[1]
    assert posted_record["media_url"] == "asset_ds/event.jpg"

    asset_payload = asset_sink.post_file.call_args.args[1]
    assert asset_payload["source_url"] == full_url


@pytest.mark.asyncio
async def test_worker_asset_resolution_multiple_url_fields():
    """All configured url_fields are resolved, not just the first."""
    records = [{"id": "1", "media_url": S3_URL_A, "thumbnail_url": S3_URL_B}]
    job = _job(asset_url_fields=json.dumps(["media_url", "thumbnail_url"]))
    main_sink = _mock_sink()
    asset_sink = _mock_sink()

    with (
        patch("databridge.adapters.get_adapter", return_value=_mock_adapter(records)),
        patch("databridge.sinks.get_sink", side_effect=_sink_factory(main_sink, asset_sink)),
        respx.mock,
    ):
        respx.get(S3_URL_A).mock(return_value=httpx.Response(200, content=ASSET_BYTES))
        respx.get(S3_URL_B).mock(return_value=httpx.Response(200, content=b"\xff\xfe"))
        await run_export_job({"pool": _Pool(job), "settings": _settings()}, str(job["id"]))

    posted_record = main_sink.post_file.call_args.args[1]
    assert posted_record["media_url"] == "asset_ds/clip.mp4"
    assert posted_record["thumbnail_url"] == "asset_ds/intro.mp4"
    # One asset stored per field
    assert asset_sink.post_file.call_count == 2


@pytest.mark.asyncio
async def test_worker_asset_resolution_missing_asset_sink_skips_download():
    """If asset_datasink_name is absent from settings, no assets are downloaded
    but records are still exported with the original URL values."""
    records = [{"id": "1", "media_url": S3_URL_A}]
    job = _job(asset_datasink_name="nonexistent-sink")
    main_sink = _mock_sink()
    asset_sink = _mock_sink()

    with (
        patch("databridge.adapters.get_adapter", return_value=_mock_adapter(records)),
        patch("databridge.sinks.get_sink", side_effect=_sink_factory(main_sink, asset_sink)),
    ):
        await run_export_job({"pool": _Pool(job), "settings": _settings()}, str(job["id"]))

    # Record still exported (asset resolution silently skipped — no asset_sink)
    assert main_sink.post_file.call_count == 1
    # URL not replaced because resolve_assets was never called
    assert main_sink.post_file.call_args.args[1]["media_url"] == S3_URL_A
    asset_sink.post_file.assert_not_called()
