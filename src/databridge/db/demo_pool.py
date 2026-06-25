from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DemoRecord(dict):
    """asyncpg.Record-compatible dict — supports row["field"] and row.get("field")."""


class _NoopTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class DemoConnection:
    """Mimics an asyncpg connection acquired from the pool."""

    def __init__(self, pool: "DemoPool") -> None:
        self._pool = pool

    def transaction(self) -> _NoopTransaction:
        return _NoopTransaction()

    async def execute(self, sql: str, *args) -> str:
        return await self._pool.execute(sql, *args)

    async def fetchval(self, sql: str, *args) -> Any:
        return await self._pool.fetchval(sql, *args)


class _AcquireCtx:
    def __init__(self, conn: DemoConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> DemoConnection:
        return self._conn

    async def __aexit__(self, *_):
        pass


class DemoPool:
    """
    In-memory substitute for asyncpg.Pool used in demo mode.

    Stores connections and export_jobs as plain dicts keyed by UUID.
    Dispatches SQL by pattern-matching rather than a full SQL engine —
    covers exactly the queries issued by db/connections.py and export/db.py.
    """

    def __init__(self) -> None:
        self._connections: dict[uuid.UUID, DemoRecord] = {}
        self._jobs: dict[uuid.UUID, DemoRecord] = {}

    # ── asyncpg.Pool protocol ──────────────────────────────────────────────

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(DemoConnection(self))

    async def close(self) -> None:
        pass

    async def fetchrow(self, sql: str, *args) -> DemoRecord | None:
        return self._dispatch(sql, args, "fetchrow")  # type: ignore[return-value]

    async def fetch(self, sql: str, *args) -> list[DemoRecord]:
        result = self._dispatch(sql, args, "fetch")
        return result or []  # type: ignore[return-value]

    async def fetchval(self, sql: str, *args) -> Any:
        return self._dispatch(sql, args, "fetchval")

    async def execute(self, sql: str, *args) -> str:
        result = self._dispatch(sql, args, "execute")
        return result if isinstance(result, str) else "OK"

    # ── SQL dispatcher ─────────────────────────────────────────────────────

    def _dispatch(self, sql: str, args: tuple, mode: str) -> Any:
        # Normalise: collapse whitespace, lowercase
        s = " ".join(sql.strip().split()).lower()

        # Health check
        if s == "select 1":
            return 1

        # Postgres-specific — no-op in demo
        if "pg_advisory_xact_lock" in s:
            return None

        # connections table
        if s.startswith("insert into connections"):
            return self._conn_insert(args)
        if s.startswith("delete from connections"):
            return self._conn_delete(args)
        if s.startswith("update connections"):
            return self._conn_update(sql, s, args, mode)
        if "from connections" in s:
            return self._conn_select(s, args, mode)

        # sync_jobs — always 0 (not used in demo)
        if "from sync_jobs" in s:
            return 0

        # export_jobs table
        if s.startswith("insert into export_jobs"):
            return self._job_insert(args)
        if "from export_jobs" in s:
            return self._job_select(s, args, mode)
        if s.startswith("update export_jobs"):
            return self._job_update(s, args)

        raise NotImplementedError(f"DemoPool: unhandled SQL: {sql[:120]!r}")

    # ── Connections ────────────────────────────────────────────────────────

    def _conn_insert(self, args: tuple) -> DemoRecord:
        # INSERT INTO connections (owner_key, label, type, role, connection_url, credentials_enc)
        row_id = uuid.uuid4()
        now = _now()
        rec = DemoRecord({
            "id": row_id,
            "owner_key": args[0],
            "label": args[1],
            "type": args[2],
            "role": args[3],
            "connection_url": args[4],
            "credentials_enc": args[5],
            "status": "untested",
            "last_tested_at": None,
            "created_at": now,
            "updated_at": now,
        })
        self._connections[row_id] = rec
        return rec

    def _conn_select(self, s: str, args: tuple, mode: str) -> Any:
        if "where id = $1 and owner_key = $2" in s:
            rec = self._connections.get(args[0])
            return rec if rec and rec["owner_key"] == args[1] else None
        if "where id = $1" in s:
            # Worker lookup — no owner check (internal use)
            return self._connections.get(args[0])
        if "where owner_key = $1" in s:
            rows = [r for r in self._connections.values() if r["owner_key"] == args[0]]
            return sorted(rows, key=lambda r: r["created_at"], reverse=True)
        return None

    def _conn_update(self, sql: str, s: str, args: tuple, mode: str) -> Any:
        if "returning *" in s:
            # Dynamic update — WHERE owner_key=$1 AND id=$2, fields follow
            owner_key, conn_id = args[0], args[1]
            rec = self._connections.get(conn_id)
            if rec is None or rec["owner_key"] != owner_key:
                return None
            new_rec = DemoRecord(rec)
            for m in re.finditer(r"(\w+)\s*=\s*\$(\d+)", sql):
                field, idx = m.group(1), int(m.group(2))
                if field not in ("owner_key", "id") and idx <= len(args):
                    new_rec[field] = args[idx - 1]
            if "status = 'untested'" in s:
                new_rec["status"] = "untested"
            if "last_tested_at = null" in s:
                new_rec["last_tested_at"] = None
            new_rec["updated_at"] = _now()
            self._connections[conn_id] = new_rec
            return new_rec
        else:
            # UPDATE connections SET status=$1, last_tested_at=$2 WHERE id=$3
            status, last_tested_at, conn_id = args[0], args[1], args[2]
            rec = self._connections.get(conn_id)
            if rec:
                new_rec = DemoRecord(rec)
                new_rec["status"] = status
                new_rec["last_tested_at"] = last_tested_at
                self._connections[conn_id] = new_rec
                return "UPDATE 1"
            return "UPDATE 0"

    def _conn_delete(self, args: tuple) -> str:
        conn_id, owner_key = args[0], args[1]
        rec = self._connections.get(conn_id)
        if rec and rec["owner_key"] == owner_key:
            del self._connections[conn_id]
            return "DELETE 1"
        return "DELETE 0"

    # ── Export jobs ────────────────────────────────────────────────────────

    def _job_insert(self, args: tuple) -> DemoRecord:
        # INSERT INTO export_jobs (17 positional fields, $1..$17)
        # Field order matches export/db.py::insert_export_job
        job_id = uuid.uuid4()
        now = _now()
        rec = DemoRecord({
            "id": job_id,
            "org_id": args[0],
            "user_id": args[1],
            "datasource_type": args[2],
            "datasource_ref": args[3],
            "datasource_filter": args[4],
            "datasink_name": args[5],
            "destination_dataset": args[6],
            "asset_resolution": args[7],
            "asset_url_fields": args[8],
            "asset_url_prefix": args[9],
            "asset_datasink_name": args[10],
            "asset_dataset": args[11],
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
            "masking_rules": args[12],
            "sampling_config": args[13],
            "webhook_url": args[14],
            "webhook_enabled": args[15],
            "webhook_payload_template": args[16],
            "external_dataset_id": None,
            "external_asset_dataset_id": None,
        })
        self._jobs[job_id] = rec
        return rec

    def _job_select(self, s: str, args: tuple, mode: str) -> Any:
        if mode == "fetchval":
            if "select status from export_jobs" in s:
                rec = self._jobs.get(args[0])
                return rec["status"] if rec else None
            if "count(*)" in s:
                return self._job_count(s, args)

        if "where id = $1" in s:
            # fetchrow — returns full record (partial selects work too since
            # the cancel logic only reads the fields it needs)
            return self._jobs.get(args[0])

        if mode == "fetch":
            return self._job_list(s, args)

        return None

    def _job_count(self, s: str, args: tuple) -> int:
        return len(self._apply_job_filters(list(self._jobs.values()), s, args))

    def _job_list(self, s: str, args: tuple) -> list[DemoRecord]:
        # Last two args are LIMIT and OFFSET; filters come before them
        limit = int(args[-2]) if len(args) >= 2 else 20
        offset = int(args[-1]) if len(args) >= 2 else 0
        filter_args = args[:-2]
        jobs = self._apply_job_filters(list(self._jobs.values()), s, filter_args)
        jobs.sort(key=lambda r: r["created_at"], reverse=True)
        return jobs[offset: offset + limit]

    def _apply_job_filters(self, jobs: list, s: str, args: tuple) -> list:
        # Extract field=$n WHERE conditions
        filters: dict[str, Any] = {}
        for m in re.finditer(r"(\w+)\s*=\s*\$(\d+)", s):
            field, idx = m.group(1), int(m.group(2))
            if idx <= len(args):
                filters[field] = args[idx - 1]
        for field, value in filters.items():
            jobs = [j for j in jobs if j.get(field) == value]

        # Literal status IN check (used by count_active_jobs_for_org)
        if "status in ('pending','running')" in s:
            jobs = [j for j in jobs if j["status"] in ("pending", "running")]

        return jobs

    def _job_update(self, s: str, args: tuple) -> str:
        if "status='cancelled'" in s:
            # UPDATE export_jobs SET status='cancelled' WHERE id=$1 AND status IN (...)
            job_id = args[0]
            rec = self._jobs.get(job_id)
            if rec and rec["status"] in ("pending", "running"):
                new_rec = DemoRecord(rec)
                new_rec["status"] = "cancelled"
                self._jobs[job_id] = new_rec
                return "UPDATE 1"
            return "UPDATE 0"

        if "status='failed'" in s:
            # Stale-job sweep from count_active_jobs_for_org
            count = 0
            if "org_id=$1" in s and len(args) >= 1:
                org_id = args[0]
                cutoff = args[1] if len(args) > 1 else None

                if "status = 'running'" in s:
                    for job_id, rec in list(self._jobs.items()):
                        if rec["org_id"] != org_id or rec["status"] != "running":
                            continue
                        if cutoff:
                            hb = rec.get("last_heartbeat_at")
                            if not (hb is None or hb < cutoff):
                                continue
                        new_rec = DemoRecord(rec)
                        new_rec.update(status="failed", error_message="job timed out (no heartbeat)")
                        self._jobs[job_id] = new_rec
                        count += 1

                elif "status = 'pending'" in s:
                    for job_id, rec in list(self._jobs.items()):
                        if rec["org_id"] != org_id or rec["status"] != "pending":
                            continue
                        ca = rec.get("created_at")
                        if cutoff and ca and ca >= cutoff:
                            continue
                        new_rec = DemoRecord(rec)
                        new_rec.update(status="failed", error_message="job timed out (never started)")
                        self._jobs[job_id] = new_rec
                        count += 1
            return f"UPDATE {count}"

        # Generic UPDATE ... WHERE id=$n
        m = re.search(r"where id=\$(\d+)", s)
        if m:
            idx = int(m.group(1))
            if idx <= len(args):
                job_id = args[idx - 1]
                rec = self._jobs.get(job_id)
                if rec:
                    new_rec = DemoRecord(rec)
                    for fm in re.finditer(r"(\w+)\s*=\s*\$(\d+)", s):
                        field, fidx = fm.group(1), int(fm.group(2))
                        if field != "id" and fidx <= len(args):
                            new_rec[field] = args[fidx - 1]
                    self._jobs[job_id] = new_rec
                    return "UPDATE 1"
        return "UPDATE 0"


class DemoArqPool:
    """
    In-process ARQ substitute for demo mode.

    Instead of enqueuing to Redis, runs export jobs directly as asyncio background
    tasks in the same event loop, sharing the DemoPool instance.
    """

    def __init__(self, pool: "DemoPool | None" = None, settings: Any = None) -> None:
        self._pool = pool
        self._settings = settings

    async def ping(self) -> bool:
        return True

    async def enqueue_job(self, function: str, *args, **kwargs) -> None:
        if function == "run_export_job" and self._pool is not None:
            import asyncio
            from databridge.export.worker import run_export_job
            job_id = args[0]
            ctx = {"pool": self._pool, "settings": self._settings}
            asyncio.create_task(run_export_job(ctx, job_id))

    async def aclose(self) -> None:
        pass
