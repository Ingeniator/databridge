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
from uuid import UUID

import asyncpg
import pytest
import pytest_asyncio

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
