from __future__ import annotations

import asyncio
import glob as _glob
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import duckdb_extension_httpfs
import httpx
import structlog

logger = structlog.get_logger(__name__)

# Pre-locate the httpfs extension binary from the pip package and LOAD it
# directly by path, so no INSTALL step (and no write to ~/.duckdb) is needed.
_HTTPFS_EXT = _glob.glob(str(duckdb_extension_httpfs.__path__[0]) + "/**/httpfs.duckdb_extension", recursive=True)[0]

_OP_MAP = {"==": "=", "!=": "!=", ">=": ">=", "<=": "<=", ">": ">", "<": "<"}
_RULE_RE = re.compile(r"(\w+)\s*(==|!=|>=|<=|>|<|contains)\s*'((?:[^'\\]|\\.)*)'", re.IGNORECASE)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(raw: Any, default: str, *, cred_name: str) -> str:
    """Validate a credential-supplied column name as a plain SQL identifier before
    it's interpolated into a query string, logging when it's rejected so a rejected
    override doesn't silently produce a plausible-but-wrong query."""
    if isinstance(raw, str) and _IDENT_RE.fullmatch(raw):
        return raw
    logger.warning("invalid_sql_identifier_column", cred_name=cred_name, value=raw, using_default=default)
    return default


def _safe_ts_col(creds: dict) -> str:
    """Return the configured timestamp column, validated as a plain SQL identifier
    before it's interpolated into a query string. `time_field`/`timestamp_column` can
    come straight from an API request (see routes/connections.py, export/worker.py), so
    anything that isn't a valid bare identifier falls back to the "timestamp" default
    rather than being spliced into SQL unsanitized. An explicit empty string is passed
    through as-is -- it means "no timestamp column" (time filtering disabled)."""
    raw = creds.get("timestamp_column", "timestamp")
    if raw == "":
        return ""
    return _safe_ident(raw, "timestamp", cred_name="timestamp_column")


def _safe_search_col(creds: dict) -> str:
    """Same validation as `_safe_ts_col`, for `search_column` -- it comes from the same
    untrusted credential dict and is spliced into SQL the same way."""
    return _safe_ident(creds.get("search_column", "message"), "message", cred_name="search_column")


def _safe_sort_col(sort_by: str | None, ts_col: str) -> str:
    """Return the column to sort preview results by. `sort_by` is a per-request override
    (picked by clicking a column header in the preview table) validated as a plain SQL
    identifier before it's interpolated into a query string; it falls back to the
    configured timestamp column when absent or invalid."""
    if not sort_by:
        return ts_col
    return _safe_ident(sort_by, ts_col, cred_name="sort_by")


def _query_to_sql(expr: str, search_column: str) -> str | None:
    """Translate a filter expression to a SQL condition fragment.

    Structured rules like ``field == 'v'`` or ``a == 'x' AND b contains 'y'``
    are converted to proper SQL equality/search conditions.  Anything that
    doesn't match the structured pattern falls back to a full-text
    ``positionCaseInsensitive`` search on *search_column*.  Returns None when
    the expression is empty.
    """
    expr = expr.strip()
    if not expr:
        return None

    tokens = re.split(r"\s+(?:AND|OR)\s+", expr, flags=re.IGNORECASE)
    logic_ops = re.findall(r"\s+(AND|OR)\s+", expr, flags=re.IGNORECASE)

    sql_parts: list[str] = []
    for token in tokens:
        m = _RULE_RE.fullmatch(token.strip())
        if not m:
            q_esc = expr.replace("'", "\\'")
            return f"positionCaseInsensitive(toString({search_column}), '{q_esc}') > 0"
        field, op, value = m.group(1), m.group(2), m.group(3)
        if op.lower() == "contains":
            sql_parts.append(f"positionCaseInsensitive(toString({field}), '{value}') > 0")
        else:
            sql_parts.append(f"{field} {_OP_MAP[op]} '{value}'")

    result = sql_parts[0]
    for i, logic in enumerate(logic_ops):
        result += f" {logic} {sql_parts[i + 1]}"
    return result

_PING_TIMEOUT = 5.0
_SCAN_TIMEOUT = 25.0
_SAMPLE_PAGE_SIZE = 1000
_SAMPLE_MAX_PAGES = 10
_SAMPLE_KEYS_PER_FORMAT = 50


class ConnectionAdapter(Protocol):
    async def ping(self) -> None: ...
    async def preview(
        self, query: str, start: datetime | None, end: datetime | None, limit: int, sort_by: str | None = None
    ) -> list[dict]: ...
    async def schema(
        self, start: datetime | None, end: datetime | None, *, nested: bool = False
    ) -> tuple[dict[str, dict], int]: ...


class ExportableAdapter(Protocol):
    async def count(self, query: str, start: datetime | None, end: datetime | None) -> int: ...
    async def fetch_page(
        self,
        query: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> list[dict]: ...


# ── Schema helpers ────────────────────────────────────────────────────────────

def _py_type(val: Any) -> str:
    if isinstance(val, bool):
        return "bool"
    if isinstance(val, int):
        return "int"
    if isinstance(val, float):
        return "float"
    if isinstance(val, list):
        return "list"
    if isinstance(val, dict):
        return "object"
    return "string"


def _infer_schema(records: list[dict]) -> dict[str, dict]:
    """Top-level field schema — matches what the preview table actually renders as columns
    and what filters can actually act on (no adapter supports nested-path filtering)."""
    schema: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        for k, v in record.items():
            if str(k).startswith("_"):
                continue
            if k not in schema:
                schema[k] = {
                    "type": _py_type(v),
                    "example": v if not isinstance(v, (dict, list)) else None,
                }
    return schema


def _infer_schema_nested(records: list[dict]) -> dict[str, dict]:
    """Recursively flattens nested objects/stringified JSON into dotted paths (e.g. body.email).
    Used only for PII candidate-field discovery: masking's dot-path resolver (export/masking.py)
    can act on nested fields even though preview/filtering cannot."""
    schema: dict[str, dict] = {}

    def _walk(obj: Any, prefix: str, depth: int) -> None:
        if depth > 3:
            return
        if isinstance(obj, str) and obj.lstrip()[:1] in ("{", "["):
            try:
                obj = json.loads(obj)
            except (json.JSONDecodeError, ValueError):
                pass
        if isinstance(obj, dict):
            for k, v in obj.items():
                if str(k).startswith("_"):
                    continue
                _walk(v, f"{prefix}{k}.", depth + 1)
        else:
            path = prefix.rstrip(".")
            if path and path not in schema:
                schema[path] = {
                    "type": _py_type(obj),
                    "example": obj if not isinstance(obj, (dict, list)) else None,
                }

    for record in records:
        _walk(record, "", 0)

    return schema


# ── Base ──────────────────────────────────────────────────────────────────────

class BaseAdapter:
    _health_path: str | None = None
    # Whether this adapter type can filter/order by an arbitrary configured
    # timestamp column at all (see apply_time_field_override below).
    supports_time_field: bool = True

    def __init__(self, conn_or_config, creds) -> None:
        self._conn = conn_or_config
        self._creds = creds

    @property
    def _url(self) -> str:
        if hasattr(self._conn, "get"):
            url = self._conn.get("connection_url", "") or self._conn.get("url", "")
        else:
            url = getattr(self._conn, "connection_url", None) or getattr(self._conn, "url", "")
        url = (url or "").rstrip("/")
        # Strip embedded credentials (user:pass@host) so they never appear in
        # request URLs, logs, or ClickHouse access logs.
        parts = urlsplit(url)
        if parts.username or parts.password:
            url = urlunsplit(parts._replace(netloc=parts.hostname + (f":{parts.port}" if parts.port else "")))
        return url

    def _creds_dict(self) -> dict:
        # For system sources conn is a dataclass — use its fields as base so
        # adapters can read user/password/database/table without special-casing.
        base: dict = {}
        if not isinstance(self._conn, dict):
            import dataclasses as _dc
            if _dc.is_dataclass(self._conn):
                base = _dc.asdict(self._conn)

        if isinstance(self._creds, dict):
            return {**base, **self._creds}
        if hasattr(self._creds, "model_dump"):
            return {**base, **self._creds.model_dump()}
        return base

    async def ping(self) -> None:
        if self._health_path is None:
            raise NotImplementedError
        async with httpx.AsyncClient(timeout=_PING_TIMEOUT) as client:
            r = await client.get(f"{self._url}{self._health_path}")
            r.raise_for_status()

    async def preview(
        self, query: str, start: datetime | None, end: datetime | None, limit: int, sort_by: str | None = None
    ) -> list[dict]:
        raise NotImplementedError

    async def count(self, query: str, start: datetime | None, end: datetime | None) -> int:
        raise NotImplementedError

    async def fetch_page(
        self,
        query: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        raise NotImplementedError

    async def schema(
        self, start: datetime | None, end: datetime | None, *, nested: bool = False
    ) -> tuple[dict[str, dict], int]:
        if start is None:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=1)

        delta = (end - start) / 5
        tasks = [
            self.preview("", start + delta * i, start + delta * (i + 1), limit=1)
            for i in range(5)
        ]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        all_records: list[dict] = []
        for res in results_list:
            if isinstance(res, list):
                all_records.extend(res)

        if not all_records:
            fallback = await self.preview("", None, None, limit=20)
            all_records = fallback

        infer = _infer_schema_nested if nested else _infer_schema
        return infer(all_records), len(all_records)


# ── ClickHouse ────────────────────────────────────────────────────────────────

class ClickHouseConnectionAdapter(BaseAdapter):
    async def ping(self) -> None:
        async with httpx.AsyncClient(timeout=_PING_TIMEOUT) as client:
            r = await client.get(f"{self._url}/ping")
            r.raise_for_status()

    async def preview(
        self, query: str, start: datetime | None, end: datetime | None, limit: int, sort_by: str | None = None
    ) -> list[dict]:
        creds = self._creds_dict()
        user = creds.get("user", "")
        password = creds.get("password", "")
        database = creds.get("database", "default")
        table = creds.get("table", "llogr_events")

        search_column = _safe_search_col(creds)
        ts_col = _safe_ts_col(creds)
        conditions: list[str] = []
        if sql_cond := _query_to_sql(query, search_column):
            conditions.append(sql_cond)
        if ts_col and start:
            conditions.append(f"{ts_col} >= parseDateTimeBestEffort('{start.isoformat()}')")
        if ts_col and end:
            conditions.append(f"{ts_col} < parseDateTimeBestEffort('{end.isoformat()}')")

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sort_col = _safe_sort_col(sort_by, ts_col)
        order_by = f" ORDER BY {sort_col} DESC" if sort_col else ""
        sql = f"SELECT * FROM {database}.{table}{where}{order_by} LIMIT {limit} FORMAT JSONEachRow"

        params: dict = {"query": sql}
        headers: dict = {}
        if user:
            headers["X-ClickHouse-User"] = user
            headers["X-ClickHouse-Key"] = password

        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(self._url + "/", params=params, headers=headers)
            r.raise_for_status()

        results: list[dict] = []
        for line in r.text.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return results

    async def count(self, query: str, start: datetime | None, end: datetime | None) -> int:
        creds = self._creds_dict()
        user = creds.get("user", "")
        password = creds.get("password", "")
        database = creds.get("database", "default")
        table = creds.get("table", "llogr_events")
        search_column = _safe_search_col(creds)
        ts_col = _safe_ts_col(creds)
        conditions: list[str] = []
        if sql_cond := _query_to_sql(query, search_column):
            conditions.append(sql_cond)
        if ts_col and start:
            conditions.append(f"{ts_col} >= parseDateTimeBestEffort('{start.isoformat()}')")
        if ts_col and end:
            conditions.append(f"{ts_col} < parseDateTimeBestEffort('{end.isoformat()}')")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT COUNT(*) FROM {database}.{table}{where} FORMAT JSONEachRow"
        params: dict = {"query": sql}
        headers: dict = {}
        if user:
            headers["X-ClickHouse-User"] = user
            headers["X-ClickHouse-Key"] = password
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(self._url + "/", params=params, headers=headers)
            r.raise_for_status()
        for line in r.text.strip().splitlines():
            line = line.strip()
            if line:
                row = json.loads(line)
                return int(next(iter(row.values())))
        return 0

    async def fetch_page(
        self,
        query: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        creds = self._creds_dict()
        user = creds.get("user", "")
        password = creds.get("password", "")
        database = creds.get("database", "default")
        table = creds.get("table", "llogr_events")
        search_column = _safe_search_col(creds)
        ts_col = _safe_ts_col(creds)
        conditions: list[str] = []
        if sql_cond := _query_to_sql(query, search_column):
            conditions.append(sql_cond)
        if ts_col and start:
            conditions.append(f"{ts_col} >= parseDateTimeBestEffort('{start.isoformat()}')")
        if ts_col and end:
            conditions.append(f"{ts_col} < parseDateTimeBestEffort('{end.isoformat()}')")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        order_by = f" ORDER BY {ts_col}" if ts_col else ""
        sql = f"SELECT * FROM {database}.{table}{where}{order_by} LIMIT {limit} OFFSET {offset} FORMAT JSONEachRow"
        params: dict = {"query": sql}
        headers: dict = {}
        if user:
            headers["X-ClickHouse-User"] = user
            headers["X-ClickHouse-Key"] = password
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(self._url + "/", params=params, headers=headers)
            r.raise_for_status()
        results: list[dict] = []
        for line in r.text.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return results

    async def schema(
        self, start: datetime | None, end: datetime | None, *, nested: bool = False
    ) -> tuple[dict[str, dict], int]:
        return await super().schema(start, end, nested=nested)


# ── Trino ─────────────────────────────────────────────────────────────────────

class TrinoConnectionAdapter(BaseAdapter):
    _health_path = "/v1/info"

    async def _execute(self, sql: str, user: str, row_limit: int | None = None) -> list[list]:
        """Execute a Trino SQL statement, following nextUri until the query finishes."""
        headers = {"X-Trino-User": user, "Content-Type": "text/plain"}
        rows: list[list] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{self._url}/v1/statement", content=sql, headers=headers)
            r.raise_for_status()
            data = r.json()
            rows.extend(data.get("data") or [])
            next_uri = data.get("nextUri")
            while next_uri:
                if row_limit is not None and len(rows) >= row_limit:
                    await client.delete(next_uri)
                    break
                r = await client.get(next_uri, headers=headers)
                r.raise_for_status()
                data = r.json()
                rows.extend(data.get("data") or [])
                state = data.get("stats", {}).get("state", "")
                next_uri = data.get("nextUri")
                if state in ("FINISHED", "FAILED") and not next_uri:
                    break
        return rows

    async def preview(
        self, query: str, start: datetime | None, end: datetime | None, limit: int, sort_by: str | None = None
    ) -> list[dict]:
        creds = self._creds_dict()
        user = creds.get("user", "trino")
        catalog = creds.get("catalog", "")
        schema_name = creds.get("schema_name", "")
        table_name = creds.get("table", "events")
        table = f"{catalog}.{schema_name}.{table_name}" if catalog and schema_name else table_name
        ts_col = _safe_ts_col(creds)
        conditions: list[str] = []
        if query:
            q_esc = query.replace("'", "''")
            conditions.append(f"CAST({_safe_search_col(creds)} AS VARCHAR) LIKE '%{q_esc}%'")
        if ts_col and start:
            conditions.append(f"{ts_col} >= TIMESTAMP '{start.strftime('%Y-%m-%d %H:%M:%S')}'")
        if ts_col and end:
            conditions.append(f"{ts_col} < TIMESTAMP '{end.strftime('%Y-%m-%d %H:%M:%S')}'")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sort_col = _safe_sort_col(sort_by, ts_col)
        order_by = f" ORDER BY {sort_col} DESC" if sort_col else ""
        sql = f"SELECT * FROM {table}{where}{order_by} LIMIT {limit}"

        headers = {"X-Trino-User": user, "Content-Type": "text/plain"}
        results: list[dict] = []
        columns: list[str] = []

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{self._url}/v1/statement", content=sql, headers=headers)
            r.raise_for_status()
            data = r.json()
            columns = [col["name"] for col in data.get("columns") or []]
            for row in data.get("data") or []:
                results.append(dict(zip(columns, row)))
            next_uri = data.get("nextUri")
            while next_uri and len(results) < limit:
                r = await client.get(next_uri, headers=headers)
                r.raise_for_status()
                data = r.json()
                if not columns:
                    columns = [col["name"] for col in data.get("columns") or []]
                for row in data.get("data") or []:
                    results.append(dict(zip(columns, row)))
                state = data.get("stats", {}).get("state", "")
                next_uri = data.get("nextUri")
                if state in ("FINISHED", "FAILED") and not next_uri:
                    break

        return results[:limit]

    async def count(self, query: str, start: datetime | None, end: datetime | None) -> int:
        creds = self._creds_dict()
        user = creds.get("user", "trino")
        catalog = creds.get("catalog", "")
        schema_name = creds.get("schema_name", "")
        table_name = creds.get("table", "events")
        table = f"{catalog}.{schema_name}.{table_name}" if catalog and schema_name else table_name
        ts_col = _safe_ts_col(creds)
        conditions: list[str] = []
        if query:
            q_esc = query.replace("'", "''")
            conditions.append(f"CAST({_safe_search_col(creds)} AS VARCHAR) LIKE '%{q_esc}%'")
        if ts_col and start:
            conditions.append(f"{ts_col} >= TIMESTAMP '{start.strftime('%Y-%m-%d %H:%M:%S')}'")
        if ts_col and end:
            conditions.append(f"{ts_col} < TIMESTAMP '{end.strftime('%Y-%m-%d %H:%M:%S')}'")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT COUNT(*) FROM {table}{where}"
        rows = await self._execute(sql, user)
        if rows:
            return int(rows[0][0])
        return 0

    async def fetch_page(
        self,
        query: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        creds = self._creds_dict()
        user = creds.get("user", "trino")
        catalog = creds.get("catalog", "")
        schema_name = creds.get("schema_name", "")
        table_name = creds.get("table", "events")
        table = f"{catalog}.{schema_name}.{table_name}" if catalog and schema_name else table_name
        ts_col = _safe_ts_col(creds)
        conditions: list[str] = []
        if query:
            q_esc = query.replace("'", "''")
            conditions.append(f"CAST({_safe_search_col(creds)} AS VARCHAR) LIKE '%{q_esc}%'")
        if ts_col and start:
            conditions.append(f"{ts_col} >= TIMESTAMP '{start.strftime('%Y-%m-%d %H:%M:%S')}'")
        if ts_col and end:
            conditions.append(f"{ts_col} < TIMESTAMP '{end.strftime('%Y-%m-%d %H:%M:%S')}'")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM {table}{where} LIMIT {limit} OFFSET {offset}"

        headers = {"X-Trino-User": user, "Content-Type": "text/plain"}
        results: list[dict] = []
        columns: list[str] = []

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{self._url}/v1/statement", content=sql, headers=headers)
            r.raise_for_status()
            data = r.json()
            columns = [col["name"] for col in data.get("columns") or []]
            for row in data.get("data") or []:
                results.append(dict(zip(columns, row)))
            next_uri = data.get("nextUri")
            while next_uri:
                r = await client.get(next_uri, headers=headers)
                r.raise_for_status()
                data = r.json()
                if not columns:
                    columns = [col["name"] for col in data.get("columns") or []]
                for row in data.get("data") or []:
                    results.append(dict(zip(columns, row)))
                state = data.get("stats", {}).get("state", "")
                next_uri = data.get("nextUri")
                if state in ("FINISHED", "FAILED") and not next_uri:
                    break

        return results

    async def schema(
        self, start: datetime | None, end: datetime | None, *, nested: bool = False
    ) -> tuple[dict[str, dict], int]:
        return await super().schema(start, end, nested=nested)


# ── Langfuse ──────────────────────────────────────────────────────────────────

class LangfuseConnectionAdapter(BaseAdapter):
    # Langfuse's API always filters traces by their own creation timestamp
    # (fromTimestamp/toTimestamp) — it has no notion of an arbitrary, user-chosen
    # timestamp column, so a per-request/per-job timestamp_column override would
    # be silently ignored if applied.
    supports_time_field = False

    # The traces endpoint's `orderBy` only accepts these field names -- unlike the
    # SQL-backed adapters, an arbitrary column can't be spliced in, so a `sort_by`
    # outside this set falls back to the timestamp default rather than sending
    # Langfuse a value it would reject.
    _SORTABLE_FIELDS = {
        "id", "timestamp", "name", "userId", "release", "version", "public", "bookmarked", "sessionId",
    }

    async def ping(self) -> None:
        async with httpx.AsyncClient(timeout=_PING_TIMEOUT) as client:
            r = await client.get(f"{self._url}/api/public/health")
            r.raise_for_status()

    def _langfuse_auth(self, creds: dict) -> tuple[str, str] | None:
        public_key = creds.get("public_key", "") or creds.get("access_key_id", "")
        secret_key = creds.get("secret_key", "") or creds.get("secret_access_key", "")
        return (public_key, secret_key) if public_key else None

    async def preview(
        self, query: str, start: datetime | None, end: datetime | None, limit: int, sort_by: str | None = None
    ) -> list[dict]:
        creds = self._creds_dict()
        order_field = sort_by if sort_by in self._SORTABLE_FIELDS else "timestamp"
        params: dict = {"limit": limit, "orderBy": f"{order_field}.desc"}
        if query:
            params["name"] = query
        if start:
            params["fromTimestamp"] = start.isoformat()
        if end:
            params["toTimestamp"] = end.isoformat()

        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{self._url}/api/public/traces", params=params, auth=self._langfuse_auth(creds))
            r.raise_for_status()
            data = r.json()
            return data.get("data", [])

    async def count(self, query: str, start: datetime | None, end: datetime | None) -> int:
        creds = self._creds_dict()
        params: dict = {"limit": 1}
        if query:
            params["name"] = query
        if start:
            params["fromTimestamp"] = start.isoformat()
        if end:
            params["toTimestamp"] = end.isoformat()
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{self._url}/api/public/traces", params=params, auth=self._langfuse_auth(creds))
            r.raise_for_status()
            data = r.json()
            return data.get("meta", {}).get("total", 0)

    async def fetch_page(
        self,
        query: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        page = (offset // limit) + 1 if limit else 1
        creds = self._creds_dict()
        params: dict = {"limit": limit, "page": page}
        if query:
            params["name"] = query
        if start:
            params["fromTimestamp"] = start.isoformat()
        if end:
            params["toTimestamp"] = end.isoformat()
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{self._url}/api/public/traces", params=params, auth=self._langfuse_auth(creds))
            r.raise_for_status()
            return r.json().get("data", [])

    async def schema(
        self, start: datetime | None, end: datetime | None, *, nested: bool = False
    ) -> tuple[dict[str, dict], int]:
        return await super().schema(start, end, nested=nested)


# ── Dataset sink ──────────────────────────────────────────────────────────────

class DatasetSinkConnectionAdapter(BaseAdapter):
    async def ping(self) -> None:
        async with httpx.AsyncClient(timeout=_PING_TIMEOUT) as client:
            r = await client.get(f"{self._url}/health")
            r.raise_for_status()

    async def preview(
        self, query: str, start: datetime | None, end: datetime | None, limit: int, sort_by: str | None = None
    ) -> list[dict]:
        raise NotImplementedError("preview not supported for sink connections")

    async def schema(
        self, start: datetime | None, end: datetime | None, *, nested: bool = False
    ) -> dict[str, dict]:
        raise NotImplementedError("schema not supported for sink connections")


# ── S3 ────────────────────────────────────────────────────────────────────────

class S3ConnectionAdapter(BaseAdapter):
    # (file extension, DuckDB reader function, extra reader options)
    # ignore_errors tolerates rows/files whose JSON/CSV shape doesn't match
    # the schema DuckDB inferred from its sample (common with independently
    # produced export files), so a single malformed record doesn't blow up
    # the whole scan once a WHERE clause forces DuckDB past LIMIT pushdown.
    _READERS = (
        ("parquet", "read_parquet", ""),
        ("jsonl", "read_json_auto", ", ignore_errors=true"),
        ("json", "read_json_auto", ", ignore_errors=true"),
        ("csv", "read_csv_auto", ", ignore_errors=true"),
    )

    def _s3_client_kwargs(self, creds: dict) -> dict:
        endpoint = getattr(self._conn, "endpoint", "") or getattr(self._conn, "connection_url", "") or creds.get("endpoint", "")
        region = creds.get("region", None) or getattr(self._conn, "region", "us-east-1")
        kwargs: dict = {
            "region_name": region,
            "aws_access_key_id": creds.get("access_key_id", ""),
            "aws_secret_access_key": creds.get("secret_access_key", ""),
        }
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        return kwargs

    async def ping(self) -> None:
        import aioboto3
        creds = self._creds_dict()
        bucket = creds.get("bucket", "") or getattr(self._conn, "bucket", "")

        async def _head():
            session = aioboto3.Session()
            async with session.client("s3", **self._s3_client_kwargs(creds)) as s3:
                await s3.head_bucket(Bucket=bucket)

        await asyncio.wait_for(_head(), timeout=_PING_TIMEOUT)

    async def _ordered_readers(self, creds: dict, bucket: str, prefix: str) -> list[tuple[str, str, str]]:
        """Peek at a few keys to guess the format, so the DuckDB scan only has to
        try the right reader instead of resolving an expensive recursive glob
        (``**/*.ext``) up to once per candidate format against a large bucket."""
        import aioboto3
        try:
            async def _list():
                session = aioboto3.Session()
                async with session.client("s3", **self._s3_client_kwargs(creds)) as s3:
                    return await s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=50)

            resp = await asyncio.wait_for(_list(), timeout=_PING_TIMEOUT)
        except Exception as exc:
            logger.debug("s3_format_detect_failed", bucket=bucket, prefix=prefix, error=str(exc))
            return list(self._READERS)

        keys = [obj["Key"].lower() for obj in resp.get("Contents", [])]
        for fmt, reader, opts in self._READERS:
            if any(key.endswith(f".{fmt}") for key in keys):
                rest = [r for r in self._READERS if r[0] != fmt]
                return [(fmt, reader, opts), *rest]
        return list(self._READERS)

    async def _sample_bucket(self, creds: dict, bucket: str, prefix: str) -> dict[str, list[str]]:
        """List up to _SAMPLE_MAX_PAGES pages under `prefix`, bucketing keys by
        extension, stopping once any known format has enough samples. This bounds
        listing cost to a fixed number of API calls regardless of bucket size --
        unlike a DuckDB recursive glob (``**/*.ext``), which must enumerate every
        matching key before it can start reading, and is what made preview/fetch_page
        time out on very large buckets. Callers should fall back to the recursive
        glob if this returns no matches at all, since the data may simply live
        further into the bucket than the sample reached."""
        import aioboto3
        by_fmt: dict[str, list[str]] = {fmt: [] for fmt, _, _ in self._READERS}
        token = None
        try:
            session = aioboto3.Session()
            async with session.client("s3", **self._s3_client_kwargs(creds)) as s3:
                for _ in range(_SAMPLE_MAX_PAGES):
                    kwargs: dict = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": _SAMPLE_PAGE_SIZE}
                    if token:
                        kwargs["ContinuationToken"] = token
                    resp = await asyncio.wait_for(s3.list_objects_v2(**kwargs), timeout=_PING_TIMEOUT)
                    for obj in resp.get("Contents", []):
                        low = obj["Key"].lower()
                        for fmt, keys in by_fmt.items():
                            if len(keys) < _SAMPLE_KEYS_PER_FORMAT and low.endswith(f".{fmt}"):
                                keys.append(obj["Key"])
                                break
                    if not resp.get("IsTruncated") or any(len(v) >= _SAMPLE_KEYS_PER_FORMAT for v in by_fmt.values()):
                        break
                    token = resp.get("NextContinuationToken")
        except Exception as exc:
            logger.debug("s3_sample_bucket_failed", bucket=bucket, prefix=prefix, error=str(exc))
        return by_fmt

    def _duckdb_con(self, creds: dict):
        import duckdb
        temp_dir = creds.get("duckdb_temp_dir", "") or "/tmp/duckdb_temp"
        os.makedirs(temp_dir, exist_ok=True)
        con = duckdb.connect(":memory:", config={"temp_directory": temp_dir})
        try:
            con.execute(f"LOAD '{_HTTPFS_EXT}';")
        except Exception:
            pass
        access_key = creds.get("access_key_id", "")
        secret_key = creds.get("secret_access_key", "")
        region = creds.get("region", "us-east-1")
        endpoint = creds.get("endpoint", "") or getattr(self._conn, "endpoint", "")
        addressing_style = creds.get("addressing_style", "virtual")
        if access_key:
            con.execute(f"SET s3_access_key_id='{access_key}';")
            con.execute(f"SET s3_secret_access_key='{secret_key}';")
        con.execute(f"SET s3_region='{region}';")
        if endpoint:
            host = endpoint.rstrip("/").split("://")[-1]
            con.execute(f"SET s3_endpoint='{host}';")
            con.execute("SET s3_use_ssl=false;")
        if addressing_style == "path":
            con.execute("SET s3_url_style='path';")
        return con

    @staticmethod
    def _time_where(creds: dict, start: datetime | None, end: datetime | None) -> str:
        ts_col = _safe_ts_col(creds)
        conditions: list[str] = []
        if ts_col and start:
            conditions.append(f"{ts_col} >= TIMESTAMP '{start.isoformat(sep=' ')}'")
        if ts_col and end:
            conditions.append(f"{ts_col} < TIMESTAMP '{end.isoformat(sep=' ')}'")
        return f" WHERE {' AND '.join(conditions)}" if conditions else ""

    async def preview(
        self, query: str, start: datetime | None, end: datetime | None, limit: int, sort_by: str | None = None
    ) -> list[dict]:
        creds = self._creds_dict()
        bucket = creds.get("bucket", "") or getattr(self._conn, "bucket", "")
        key_prefix = creds.get("key_prefix", "") or getattr(self._conn, "key_prefix", "")
        where = self._time_where(creds, start, end)
        sort_col = _safe_sort_col(sort_by, _safe_ts_col(creds))
        order_by = f" ORDER BY {sort_col} DESC" if sort_col else ""
        prefix = key_prefix.rstrip("/") + "/" if key_prefix else ""
        by_fmt = await self._sample_bucket(creds, bucket, prefix)
        readers = [(fmt, reader, opts) for fmt, reader, opts in self._READERS if by_fmt.get(fmt)]
        # If the bounded sample above found no matching keys, the data may simply
        # live further into the bucket than the sample reached -- fall back to the
        # slower recursive glob rather than silently reporting an empty preview.
        fallback_readers = list(self._READERS) if not readers else []

        def _scan() -> list[dict]:
            con = self._duckdb_con(creds)
            for fmt, reader, opts in readers:
                paths = ", ".join(f"'s3://{bucket}/{key}'" for key in by_fmt[fmt])
                try:
                    rows = con.execute(
                        f"SELECT * FROM {reader}([{paths}]{opts}){where}{order_by} LIMIT {limit}"
                    ).fetchall()
                    cols = [d[0] for d in (con.description or [])]
                    return [dict(zip(cols, row)) for row in rows]
                except Exception as exc:
                    logger.debug("s3_reader_failed", bucket=bucket, fmt=fmt, error=str(exc))
                    continue
            for fmt, reader, opts in fallback_readers:
                path = f"s3://{bucket}/{prefix}**/*.{fmt}"
                try:
                    rows = con.execute(
                        f"SELECT * FROM {reader}('{path}'{opts}){where}{order_by} LIMIT {limit}"
                    ).fetchall()
                    cols = [d[0] for d in (con.description or [])]
                    return [dict(zip(cols, row)) for row in rows]
                except Exception as exc:
                    logger.debug("s3_reader_failed", bucket=bucket, fmt=fmt, error=str(exc))
                    continue
            return []

        try:
            return await asyncio.wait_for(asyncio.to_thread(_scan), timeout=_SCAN_TIMEOUT)
        except asyncio.TimeoutError:
            raise TimeoutError(f"S3 scan of bucket '{bucket}' timed out after {_SCAN_TIMEOUT:.0f}s")

    async def count(self, query: str, start: datetime | None, end: datetime | None) -> int:
        creds = self._creds_dict()
        bucket = creds.get("bucket", "") or getattr(self._conn, "bucket", "")
        key_prefix = creds.get("key_prefix", "") or getattr(self._conn, "key_prefix", "")
        where = self._time_where(creds, start, end)
        prefix = key_prefix.rstrip("/") + "/" if key_prefix else ""
        readers = await self._ordered_readers(creds, bucket, prefix)

        def _count() -> int:
            con = self._duckdb_con(creds)
            for fmt, reader, opts in readers:
                path = f"s3://{bucket}/{prefix}**/*.{fmt}"
                try:
                    row = con.execute(f"SELECT COUNT(*) FROM {reader}('{path}'{opts}){where}").fetchone()
                    return int(row[0]) if row else 0
                except Exception as exc:
                    logger.debug("s3_reader_failed", bucket=bucket, fmt=fmt, error=str(exc))
                    continue
            return 0

        try:
            return await asyncio.wait_for(asyncio.to_thread(_count), timeout=_SCAN_TIMEOUT)
        except asyncio.TimeoutError:
            raise TimeoutError(f"S3 scan of bucket '{bucket}' timed out after {_SCAN_TIMEOUT:.0f}s")

    async def fetch_page(
        self,
        query: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        creds = self._creds_dict()
        bucket = creds.get("bucket", "") or getattr(self._conn, "bucket", "")
        key_prefix = creds.get("key_prefix", "") or getattr(self._conn, "key_prefix", "")
        where = self._time_where(creds, start, end)
        ts_col = _safe_ts_col(creds)
        order_by = f" ORDER BY {ts_col}" if ts_col else ""
        prefix = key_prefix.rstrip("/") + "/" if key_prefix else ""
        by_fmt = await self._sample_bucket(creds, bucket, prefix)
        readers = [(fmt, reader, opts) for fmt, reader, opts in self._READERS if by_fmt.get(fmt)]
        # See preview(): fall back to the recursive glob if the bounded sample
        # found nothing, rather than silently reporting an empty page.
        fallback_readers = list(self._READERS) if not readers else []

        def _scan() -> list[dict]:
            con = self._duckdb_con(creds)
            for fmt, reader, opts in readers:
                paths = ", ".join(f"'s3://{bucket}/{key}'" for key in by_fmt[fmt])
                try:
                    rows = con.execute(
                        f"SELECT * FROM {reader}([{paths}]{opts}){where}{order_by} LIMIT {limit} OFFSET {offset}"
                    ).fetchall()
                    cols = [d[0] for d in (con.description or [])]
                    return [dict(zip(cols, row)) for row in rows]
                except Exception as exc:
                    logger.debug("s3_reader_failed", bucket=bucket, fmt=fmt, error=str(exc))
                    continue
            for fmt, reader, opts in fallback_readers:
                path = f"s3://{bucket}/{prefix}**/*.{fmt}"
                try:
                    rows = con.execute(
                        f"SELECT * FROM {reader}('{path}'{opts}){where}{order_by} LIMIT {limit} OFFSET {offset}"
                    ).fetchall()
                    cols = [d[0] for d in (con.description or [])]
                    return [dict(zip(cols, row)) for row in rows]
                except Exception as exc:
                    logger.debug("s3_reader_failed", bucket=bucket, fmt=fmt, error=str(exc))
                    continue
            return []

        try:
            return await asyncio.wait_for(asyncio.to_thread(_scan), timeout=_SCAN_TIMEOUT)
        except asyncio.TimeoutError:
            raise TimeoutError(f"S3 scan of bucket '{bucket}' timed out after {_SCAN_TIMEOUT:.0f}s")

    async def schema(
        self, start: datetime | None, end: datetime | None, *, nested: bool = False
    ) -> tuple[dict[str, dict], int]:
        records = await self.preview("", start, end, limit=20)
        infer = _infer_schema_nested if nested else _infer_schema
        return infer(records), len(records)


# ── Registry and factory ──────────────────────────────────────────────────────

_REGISTRY: dict[str, type[BaseAdapter]] = {
    "clickhouse": ClickHouseConnectionAdapter,
    "trino": TrinoConnectionAdapter,
    "langfuse": LangfuseConnectionAdapter,
    "dataset": DatasetSinkConnectionAdapter,
    "s3": S3ConnectionAdapter,
}


def get_adapter(conn_or_config, creds) -> ConnectionAdapter:
    """Single dispatch point. Registry lookup only — no type-branch logic here."""
    source_type = (
        conn_or_config.get("type")
        if hasattr(conn_or_config, "get")
        else getattr(conn_or_config, "type", None)
    )
    cls = _REGISTRY.get(source_type)
    if cls is None:
        raise ValueError(f"Unknown connection type: {source_type!r}")
    return cls(conn_or_config, creds)


def apply_time_field_override(
    adapter: ConnectionAdapter, conn_or_config, creds: dict, time_field: str | None
) -> tuple[ConnectionAdapter, dict]:
    """Rebuild `adapter` with `timestamp_column` overridden to `time_field`, unless
    `time_field` wasn't supplied or the adapter type can't honor a per-request/per-job
    timestamp column at all (see `BaseAdapter.supports_time_field`)."""
    if time_field is None or not getattr(adapter, "supports_time_field", True):
        return adapter, creds
    creds = {**creds, "timestamp_column": time_field}
    return get_adapter(conn_or_config, creds), creds
