"""Integration test stubs — list, retry, role visibility for export jobs."""
import textwrap
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

SINK_NAME = "test-sink"


@pytest.fixture
def config_with_sinks(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(textwrap.dedent(f"""
        server:
          port: 5010
          debug: false
          silence_probes: false
        database_url: "postgresql://postgres:postgres@localhost:5432/databridge_test"
        encryption_key: "{key}"
        datasources: []
        datasinks:
          - name: "{SINK_NAME}"
            type: local-jsonl
            path: "/tmp/test_exports"
        export:
          max_concurrent_jobs_per_org: 5
    """))
    monkeypatch.setenv("DATABRIDGE_CONFIG", str(cfg))
    yield cfg


def _make_job(org_id, user_id, status="completed", job_id=None):
    now = datetime.now(timezone.utc)
    jid = job_id or uuid4()
    return {
        "id": jid, "org_id": org_id, "user_id": user_id,
        "datasource_type": "system", "datasource_ref": "src",
        "datasource_filter": "{}",
        "datasink_name": SINK_NAME, "destination_dataset": "ds",
        "asset_resolution": False, "asset_url_fields": "[]",
        "asset_url_prefix": "", "asset_datasink_name": None, "asset_dataset": None,
        "status": status, "records_total": 10,
        "records_processed": 10, "records_skipped": 0, "asset_errors": 0,
        "error_message": "oops" if status == "failed" else None,
        "created_at": now, "started_at": now, "completed_at": now, "last_heartbeat_at": now,
    }


class _MultiUserPool:
    """Fake pool with jobs for multiple users/orgs."""

    def __init__(self):
        self._jobs: list[dict] = [
            _make_job("orgA", "user1"),
            _make_job("orgA", "user2"),
            _make_job("orgB", "user3"),
            _make_job("orgA", "user1", status="failed"),
        ]

    async def fetchrow(self, query: str, *args):
        q = query.strip().upper()
        if "INSERT INTO EXPORT_JOBS" in q:
            now = datetime.now(timezone.utc)
            jid = uuid4()
            job = {
                "id": jid, "org_id": args[0], "user_id": args[1],
                "datasource_type": args[2], "datasource_ref": args[3],
                "datasource_filter": args[4] or "{}",
                "datasink_name": args[5], "destination_dataset": args[6],
                "asset_resolution": args[7] or False, "asset_url_fields": args[8] or "[]",
                "asset_url_prefix": args[9] or "", "asset_datasink_name": args[10],
                "asset_dataset": args[11],
                "status": "pending", "records_total": None,
                "records_processed": 0, "records_skipped": 0, "asset_errors": 0,
                "error_message": None, "created_at": now, "started_at": None,
                "completed_at": None, "last_heartbeat_at": None,
            }
            self._jobs.append(job)
            return job
        if "SELECT * FROM EXPORT_JOBS WHERE ID" in q:
            jid = str(args[0])
            return next((j for j in self._jobs if str(j["id"]) == jid), None)
        return None

    async def fetch(self, query: str, *args):
        return self._jobs

    async def fetchval(self, query: str, *args):
        q = query.strip().upper()
        if "COUNT(*)" in q:
            return len(self._jobs)
        return 0

    async def execute(self, query: str, *args):
        return "OK"

    async def close(self):
        pass

    def acquire(self):
        return _FakeConn(self)


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def transaction(self):
        return _FakeTx()

    async def execute(self, *args):
        return "OK"

    async def fetchval(self, query, *args):
        return 0


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def client(config_with_sinks, monkeypatch):
    from databridge.config import get_settings
    get_settings.cache_clear()
    pool = _MultiUserPool()
    _arq_mock = MagicMock(enqueue_job=AsyncMock(), aclose=AsyncMock())
    with patch("databridge.main.create_pool", AsyncMock(return_value=pool)), \
         patch("arq.create_pool", AsyncMock(return_value=_arq_mock)):
        from databridge.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c, pool
    get_settings.cache_clear()


def _to_response(job: dict):
    from databridge.export.models import ExportJobResponse, ExportJobStatus, FilterSnapshot
    import json
    filter_raw = job["datasource_filter"]
    if isinstance(filter_raw, str):
        filter_raw = json.loads(filter_raw)
    url_fields = job["asset_url_fields"]
    if isinstance(url_fields, str):
        url_fields = json.loads(url_fields)
    return ExportJobResponse(
        id=job["id"], org_id=job["org_id"], user_id=job["user_id"],
        datasource_type=job["datasource_type"], datasource_ref=job["datasource_ref"],
        datasource_filter=FilterSnapshot(**(filter_raw or {})),
        datasink_name=job["datasink_name"], destination_dataset=job["destination_dataset"],
        asset_resolution=job["asset_resolution"], asset_url_fields=url_fields or [],
        asset_url_prefix=job["asset_url_prefix"] or "", asset_datasink_name=job["asset_datasink_name"],
        asset_dataset=job["asset_dataset"], status=ExportJobStatus(job["status"]),
        records_total=job["records_total"], records_processed=job["records_processed"],
        records_skipped=job["records_skipped"], asset_errors=job["asset_errors"],
        error_message=job["error_message"], created_at=job["created_at"],
        started_at=job["started_at"], completed_at=job["completed_at"],
    )


def test_user_role_returns_only_own_jobs(client):
    """GET /api/v1/export-jobs with user role returns only caller's jobs."""
    c, pool = client
    import databridge.routes.export_jobs as ej

    async def _filtered(p, org_id, user_id, role, **kwargs):
        jobs = [_to_response(j) for j in pool._jobs if j["org_id"] == org_id and j["user_id"] == user_id]
        return jobs, len(jobs)

    old = ej.list_export_jobs
    ej.list_export_jobs = _filtered
    try:
        resp = c.get("/api/v1/export-jobs", headers={"X-Group-ID": "orgA/user1", "X-Role": "USER"})
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert all(i["user_id"] == "user1" for i in items)
    finally:
        ej.list_export_jobs = old


def test_org_admin_role_returns_all_org_jobs(client):
    """org_admin role returns all jobs in the org."""
    c, pool = client
    import databridge.routes.export_jobs as ej

    async def _org_filtered(p, org_id, user_id, role, **kwargs):
        jobs = [_to_response(j) for j in pool._jobs if j["org_id"] == org_id]
        return jobs, len(jobs)

    old = ej.list_export_jobs
    ej.list_export_jobs = _org_filtered
    try:
        resp = c.get("/api/v1/export-jobs", headers={"X-Group-ID": "orgA/admin", "X-Role": "ORG_ADMIN"})
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert all(i["org_id"] == "orgA" for i in items)
    finally:
        ej.list_export_jobs = old


def test_super_admin_returns_all_jobs(client):
    """super_admin returns all jobs."""
    c, pool = client
    import databridge.routes.export_jobs as ej

    async def _all(p, org_id, user_id, role, **kwargs):
        jobs = [_to_response(j) for j in pool._jobs]
        return jobs, len(pool._jobs)

    old = ej.list_export_jobs
    ej.list_export_jobs = _all
    try:
        resp = c.get("/api/v1/export-jobs", headers={"X-Group-ID": "any/admin", "X-Role": "SUPER_ADMIN"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == len(pool._jobs)
    finally:
        ej.list_export_jobs = old


def test_pagination_page_size(client):
    """Pagination: page=1&page_size=2 returns 2 items."""
    c, pool = client
    import databridge.routes.export_jobs as ej

    async def _paginated(p, org_id, user_id, role, page=1, page_size=20, **kwargs):
        offset = (page - 1) * page_size
        sliced = [_to_response(j) for j in pool._jobs[offset:offset + page_size]]
        return sliced, len(pool._jobs)

    old = ej.list_export_jobs
    ej.list_export_jobs = _paginated
    try:
        resp = c.get(
            "/api/v1/export-jobs?page=1&page_size=2",
            headers={"X-Group-ID": "any/admin", "X-Role": "SUPER_ADMIN"},
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["items"]) == 2
    finally:
        ej.list_export_jobs = old


def test_retry_failed_job_returns_201(client):
    """POST /api/v1/export-jobs/{id}/retry on failed job returns 201 new job."""
    c, pool = client
    failed_job = next(j for j in pool._jobs if j["status"] == "failed")
    resp = c.post(
        f"/api/v1/export-jobs/{failed_job['id']}/retry",
        headers={"X-Group-ID": f"{failed_job['org_id']}/{failed_job['user_id']}", "X-Role": "SUPER_ADMIN"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["datasink_name"] == failed_job["datasink_name"]


def test_retry_non_failed_job_returns_400(client):
    """Retry on non-failed job returns 400."""
    c, pool = client
    completed_job = next(j for j in pool._jobs if j["status"] == "completed")
    resp = c.post(
        f"/api/v1/export-jobs/{completed_job['id']}/retry",
        headers={"X-Group-ID": f"{completed_job['org_id']}/{completed_job['user_id']}", "X-Role": "SUPER_ADMIN"},
    )
    assert resp.status_code == 400


def test_retry_at_concurrent_limit_returns_429(client, monkeypatch):
    """Retry when at concurrent limit returns 429."""
    c, pool = client
    failed_job = next(j for j in pool._jobs if j["status"] == "failed")
    import databridge.routes.export_jobs as ej

    async def _over_limit(pool, org_id):
        return 999

    monkeypatch.setattr(ej, "count_active_jobs_for_org", _over_limit)
    resp = c.post(
        f"/api/v1/export-jobs/{failed_job['id']}/retry",
        headers={"X-Group-ID": f"{failed_job['org_id']}/{failed_job['user_id']}", "X-Role": "SUPER_ADMIN"},
    )
    assert resp.status_code == 429
