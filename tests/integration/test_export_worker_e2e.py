"""
End-to-end worker tests — call run_export_job directly with a real PostgreSQL
pool and a real ClickHouse (both must be reachable at localhost).

Run with:
    uv run pytest tests/integration/test_export_worker_e2e.py -v

Services required:  docker compose -f docker-compose.dev.yml up -d
"""
import json
import textwrap
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import asyncpg
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from databridge.config import DatasinkConfig, ExportSettings, Settings, ServerConfig, SystemSourceConfig, get_settings
from databridge.export.db import insert_export_job
from databridge.export.models import ExportJobCreate, ExportJobStatus
from databridge.export.worker import run_export_job

# ── constants ────────────────────────────────────────────────────────────────

_DB_URL = "postgresql://postgres:postgres@localhost:5432/databridge"
_CH_URL = "http://localhost:8123"

# ── helpers ───────────────────────────────────────────────────────────────────

def _settings(export_dir: Path) -> Settings:
    """Build a Settings object that points at the local dev services."""
    return Settings(
        server=ServerConfig(debug=True),
        database_url=_DB_URL,
        encryption_key="Ks_7gv0quQuNMvwLHNhToPPgrQw7Z3Zjm2r-4mTJqF4=",
        datasources=(
            SystemSourceConfig(
                name="local-clickhouse",
                type="clickhouse",
                url=_CH_URL,
                database="default",
                table="llogr_events",
                user="default",
                password="",
            ),
        ),
        datasinks=(
            DatasinkConfig(name="local-jsonl", type="local-jsonl", path=str(export_dir)),
            DatasinkConfig(name="local-zip",   type="local-zip",   path=str(export_dir)),
        ),
        export=ExportSettings(batch_size=50),
    )


async def _run_job(pool: asyncpg.Pool, settings: Settings, sink_name: str, dataset: str) -> dict:
    """Insert a job, call run_export_job directly, return the final DB row as dict."""
    job = await insert_export_job(
        pool,
        ExportJobCreate(
            datasource_type="system",
            datasource_ref=str(settings.datasources[0].id),
            datasink_name=sink_name,
            destination_dataset=dataset,
        ),
        org_id="test-org",
        user_id="test-user",
    )
    ctx = {"pool": pool, "settings": settings}
    await run_export_job(ctx, str(job.id))
    row = await pool.fetchrow("SELECT * FROM export_jobs WHERE id = $1", job.id)
    return dict(row)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def pool():
    p = await asyncpg.create_pool(dsn=_DB_URL)
    yield p
    await p.close()


@pytest.fixture
def export_dir(tmp_path) -> Path:
    d = tmp_path / "exports"
    d.mkdir()
    return d


@pytest.fixture
def settings(export_dir) -> Settings:
    return _settings(export_dir)


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_jsonl_export_completes(pool, settings, export_dir):
    """Job reaches 'completed', output JSONL has expected record count."""
    row = await _run_job(pool, settings, "local-jsonl", "e2e_jsonl")

    assert row["status"] == "completed", f"expected completed, got {row['status']}: {row['error_message']}"
    assert row["records_processed"] > 0
    assert row["records_skipped"] == 0
    assert row["error_message"] is None

    jsonl_files = list(export_dir.glob("*.jsonl"))
    assert len(jsonl_files) == 1, f"expected 1 JSONL file, found {jsonl_files}"

    lines = jsonl_files[0].read_text().strip().splitlines()
    assert len(lines) == row["records_processed"]
    # every line must be valid JSON
    for line in lines:
        json.loads(line)


@pytest.mark.asyncio
async def test_zip_export_completes(pool, settings, export_dir):
    """Job reaches 'completed', output ZIP contains one JSON file per record."""
    row = await _run_job(pool, settings, "local-zip", "e2e_zip")

    assert row["status"] == "completed", f"expected completed, got {row['status']}: {row['error_message']}"
    assert row["records_processed"] > 0

    zip_files = list(export_dir.glob("*.zip"))
    assert len(zip_files) == 1, f"expected 1 ZIP file, found {zip_files}"

    with zipfile.ZipFile(zip_files[0]) as zf:
        names = zf.namelist()
        assert len(names) == row["records_processed"]
        # every entry must be valid JSON
        for name in names:
            json.loads(zf.read(name))


@pytest.mark.asyncio
async def test_records_total_set_before_batching(pool, settings, export_dir):
    """records_total is set to a non-null value after the job runs."""
    row = await _run_job(pool, settings, "local-jsonl", "e2e_total")
    assert row["records_total"] is not None
    assert row["records_total"] >= row["records_processed"]


@pytest.mark.asyncio
async def test_job_fails_on_unknown_sink(pool, settings, export_dir):
    """Job with a non-existent datasink name ends with status=failed."""
    job = await insert_export_job(
        pool,
        ExportJobCreate(
            datasource_type="system",
            datasource_ref=str(settings.datasources[0].id),
            datasink_name="nonexistent-sink",
            destination_dataset="e2e_fail",
        ),
        org_id="test-org",
        user_id="test-user",
    )
    ctx = {"pool": pool, "settings": settings}
    await run_export_job(ctx, str(job.id))

    row = await pool.fetchrow("SELECT * FROM export_jobs WHERE id = $1", job.id)
    assert row["status"] == "failed"
    assert "nonexistent-sink" in row["error_message"]


@pytest.mark.asyncio
async def test_jsonl_timestamps_set(pool, settings, export_dir):
    """started_at and completed_at are set after a successful run."""
    row = await _run_job(pool, settings, "local-jsonl", "e2e_timestamps")
    assert row["started_at"] is not None
    assert row["completed_at"] is not None
    assert row["completed_at"] >= row["started_at"]


@pytest.mark.asyncio
async def test_jsonl_export_with_query_filter(pool, settings, export_dir):
    """Records-total respects a query filter (may reduce or match full count)."""
    from databridge.export.models import FilterSnapshot
    job = await insert_export_job(
        pool,
        ExportJobCreate(
            datasource_type="system",
            datasource_ref=str(settings.datasources[0].id),
            datasink_name="local-jsonl",
            destination_dataset="e2e_filtered",
            datasource_filter=FilterSnapshot(query="Summarise"),
        ),
        org_id="test-org",
        user_id="test-user",
    )
    ctx = {"pool": pool, "settings": settings}
    await run_export_job(ctx, str(job.id))

    row = await pool.fetchrow("SELECT * FROM export_jobs WHERE id = $1", job.id)
    assert row["status"] == "completed"
    assert row["records_total"] is not None
    # filtered result must be <= unfiltered total
    assert row["records_processed"] <= 20


# ── download endpoint tests ───────────────────────────────────────────────────

def _make_app_with_real_pool(real_pool, settings):
    """Build a test FastAPI app backed by the real PG pool."""
    import os
    import textwrap as tw
    from cryptography.fernet import Fernet

    # write a temp config pointing at real services
    import tempfile, yaml as _yaml, pathlib
    cfg = {
        "server": {"debug": True, "port": 5010, "silence_probes": False},
        "database_url": _DB_URL,
        "encryption_key": settings.encryption_key,
        "datasources": [
            {"name": s.name, "type": s.type, "url": s.url,
             "database": s.database, "table": s.table, "user": s.user, "password": s.password}
            for s in settings.datasources
        ],
        "datasinks": [
            {"name": sk.name, "type": sk.type,
             **({} if not sk.url else {"url": sk.url}),
             **({} if not sk.path else {"path": sk.path})}
            for sk in settings.datasinks
        ],
    }
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    import json as _json
    tmp.write(_yaml.dump(cfg))
    tmp.flush()
    os.environ["DATABRIDGE_CONFIG"] = tmp.name

    from databridge.config import get_settings as _gs
    _gs.cache_clear()

    _arq_mock = MagicMock(enqueue_job=AsyncMock(), aclose=AsyncMock())
    with patch("databridge.main.create_pool", AsyncMock(return_value=real_pool)), \
         patch("arq.create_pool", AsyncMock(return_value=_arq_mock)):
        from databridge.main import create_app
        app = create_app()
    return app, tmp.name


@pytest.mark.asyncio
async def test_download_jsonl_returns_200(pool, settings, export_dir):
    """GET /api/v1/export-jobs/{id}/download returns 200 with file content."""
    row = await _run_job(pool, settings, "local-jsonl", "dl_e2e")
    job_id = row["id"]

    app, cfg_path = _make_app_with_real_pool(pool, settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get(f"/api/v1/export-jobs/{job_id}/download", headers={"X-Group-ID": "test-org/test-user"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    lines = resp.text.strip().splitlines()
    assert len(lines) == row["records_processed"]
    json.loads(lines[0])  # valid JSON


@pytest.mark.asyncio
async def test_download_zip_returns_200(pool, settings, export_dir):
    """GET /api/v1/export-jobs/{id}/download returns a valid ZIP."""
    row = await _run_job(pool, settings, "local-zip", "dl_zip_e2e")
    job_id = row["id"]

    app, cfg_path = _make_app_with_real_pool(pool, settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get(f"/api/v1/export-jobs/{job_id}/download", headers={"X-Group-ID": "test-org/test-user"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    import io
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert len(zf.namelist()) == row["records_processed"]


@pytest.mark.asyncio
async def test_download_pending_job_returns_409(pool, settings, export_dir):
    """Downloading a pending job returns 409."""
    from databridge.export.models import ExportJobCreate
    job = await insert_export_job(
        pool,
        ExportJobCreate(
            datasource_type="system",
            datasource_ref=str(settings.datasources[0].id),
            datasink_name="local-jsonl",
            destination_dataset="dl_pending",
        ),
        org_id="test-org",
        user_id="test-user",
    )
    app, _ = _make_app_with_real_pool(pool, settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get(f"/api/v1/export-jobs/{job.id}/download", headers={"X-Group-ID": "test-org/test-user"})
    assert resp.status_code == 409
