#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx
import yaml


DEFAULT_CHUNK_RECORDS = 1_000


@dataclass
class Operation:
    method: str
    path: str
    operation_id: str


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="databridge-relay",
        description="Relay data from user-reachable sources into Databridge.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    openapi = subparsers.add_parser("openapi", help="Run an OpenAPI relay scenario")
    openapi.add_argument("--config", required=True, help="YAML or JSON scenario file")
    openapi.add_argument("--dry-run", action="store_true", help="Read source data without uploading")
    openapi.add_argument("--limit", type=int, default=None, help="Maximum records to relay")
    openapi.add_argument(
        "--verbose",
        action="store_true",
        help="Print each relayed record as compact JSON to stdout",
    )

    args = parser.parse_args()
    if args.command == "openapi":
        return run_openapi(args.config, dry_run=args.dry_run, limit=args.limit, verbose=args.verbose)
    return 2


def run_openapi(config_path: str, *, dry_run: bool, limit: int | None, verbose: bool = False) -> int:
    config = load_config(config_path)
    source = config["source"]
    destination = config["destination"]
    transfer = config.get("transfer", {})

    if source.get("type") != "openapi":
        raise SystemExit("source.type must be 'openapi'")

    _validate_paths(source)

    spec = load_json(source["spec"], auth=source.get("specAuth"))
    operation = find_operation(spec, source["operationId"])
    base_url = source.get("baseUrl") or first_server_url(spec)
    if not base_url:
        raise SystemExit("baseUrl is required when the OpenAPI spec has no servers[0].url")

    chunk_records = int(transfer.get("chunkRecords", DEFAULT_CHUNK_RECORDS))
    record_path = source.get("output", {}).get("recordPath", "$")
    output_format = source.get("output", {}).get("format", "jsonl")
    if output_format != "jsonl":
        raise SystemExit("Only output.format=jsonl is supported by this sketch")

    with (
        httpx.Client(timeout=httpx.Timeout(60.0)) as source_client,
        httpx.Client(timeout=httpx.Timeout(60.0)) as bridge_client,
    ):
        session_id: str | None = None
        if not dry_run:
            session_id = create_relay_session(bridge_client, config, operation)
            print(f"created relay session {session_id}", file=sys.stderr)

        uploaded = 0
        chunk_index = 0
        chunk: list[dict[str, Any]] = []
        completed = False

        try:
            for record in iter_openapi_records(
                source_client,
                base_url,
                source,
                operation,
                record_path,
                verbose=verbose,
            ):
                if verbose:
                    print(json.dumps(record, separators=(",", ":")))
                chunk.append(record)
                uploaded += 1

                if len(chunk) >= chunk_records:
                    if dry_run:
                        print(f"read chunk {chunk_index}: {len(chunk)} records", file=sys.stderr)
                    else:
                        upload_jsonl_chunk(bridge_client, config, session_id, chunk_index, chunk)
                        print(f"uploaded chunk {chunk_index}: {len(chunk)} records", file=sys.stderr)
                    chunk_index += 1
                    chunk = []

                if limit is not None and uploaded >= limit:
                    break

            if chunk:
                if dry_run:
                    print(f"read chunk {chunk_index}: {len(chunk)} records", file=sys.stderr)
                else:
                    upload_jsonl_chunk(bridge_client, config, session_id, chunk_index, chunk)
                    print(f"uploaded chunk {chunk_index}: {len(chunk)} records", file=sys.stderr)

            if not dry_run:
                complete_relay_session(bridge_client, config, session_id, uploaded)
                completed = True
        finally:
            if not dry_run and session_id is not None and not completed:
                abort_relay_session(bridge_client, config, session_id)

    print(f"{'read' if dry_run else 'relayed'} {uploaded} records", file=sys.stderr)
    return 0


def _validate_paths(source: dict[str, Any]) -> None:
    paths = [source.get("output", {}).get("recordPath", "$")]
    pagination = source.get("pagination", {})
    for key in ("nextCursorPath", "itemsPath", "nextLinkPath"):
        if key in pagination:
            paths.append(pagination[key])
    for path in paths:
        if path and path not in {"", "$"} and not path.startswith("$."):
            raise SystemExit(f"Unsupported JSONPath expression (must start with '$.'): {path!r}")


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        if path.endswith(".json"):
            return json.load(fh)
        return yaml.safe_load(fh)


def load_json(location: str, *, auth: dict[str, Any] | None = None) -> dict[str, Any]:
    if location.startswith(("http://", "https://")):
        response = httpx.get(location, headers=auth_headers(auth), timeout=30.0)
        response.raise_for_status()
        return response.json()
    with open(location, "r", encoding="utf-8") as fh:
        return json.load(fh)


def first_server_url(spec: dict[str, Any]) -> str | None:
    servers = spec.get("servers") or []
    if servers:
        return servers[0].get("url")
    host = spec.get("host")
    if host:
        scheme = (spec.get("schemes") or ["https"])[0]
        base_path = spec.get("basePath", "")
        return f"{scheme}://{host}{base_path}"
    return None


def find_operation(spec: dict[str, Any], operation_id: str) -> Operation:
    for path, path_item in (spec.get("paths") or {}).items():
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if operation.get("operationId") == operation_id:
                return Operation(method=method.upper(), path=path, operation_id=operation_id)
    raise SystemExit(f"operationId not found in spec: {operation_id}")


def iter_openapi_records(
    client: httpx.Client,
    base_url: str,
    source: dict[str, Any],
    operation: Operation,
    record_path: str,
    *,
    verbose: bool = False,
) -> Iterator[dict[str, Any]]:
    pagination = source.get("pagination", {"type": "none"})
    params = dict(source.get("parameters") or {})
    body = source.get("body")
    page_count = 0
    next_url: str | None = None  # used only for nextLink pagination

    while True:
        page_count += 1
        if next_url is not None:
            # nextLink URL is self-contained; only re-attach auth query params.
            url = _resolve_url(next_url, base_url)
            request_params = dict(auth_query_params(source.get("auth")))
        else:
            path, request_params = build_request_path(operation.path, params)
            request_params.update({k: v for k, v in params.items() if "{" + k + "}" not in operation.path})
            request_params.update(auth_query_params(source.get("auth")))
            url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))

        headers = auth_headers(source.get("auth"))
        request_json = body if operation.method in {"POST", "PUT", "PATCH"} else None
        if verbose:
            log_request(operation.method, url, request_params, headers, request_json)

        response = client.request(
            operation.method,
            url,
            params=request_params,
            json=request_json,
            headers=headers,
        )
        payload = response_payload(response)
        if verbose:
            log_response(response, payload)
        response.raise_for_status()

        records = json_select(payload, record_path)
        if isinstance(records, list):
            for item in records:
                yield ensure_object_record(item)
        elif records is not None:
            yield ensure_object_record(records)

        max_pages = pagination.get("maxPages")
        if max_pages is not None and page_count >= int(max_pages):
            break

        next_url, next_value = next_pagination_value(payload, response, pagination, params, page_count)
        if next_url is None and next_value is None:
            break
        if next_value is not None:
            apply_next_pagination(params, pagination, next_value)


def _resolve_url(url: str, base_url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(base_url.rstrip("/") + "/", url.lstrip("/"))


def build_request_path(path_template: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    path = path_template
    used: set[str] = set()
    for key, value in params.items():
        token = "{" + key + "}"
        if token in path:
            path = path.replace(token, quote(str(value), safe=""))
            used.add(key)
    return path, {k: v for k, v in params.items() if k not in used}


def _basic_token(username: str, password: str) -> str:
    return base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")


def auth_headers(auth: dict[str, Any] | None) -> dict[str, str]:
    if not auth:
        return {}
    auth_type = auth.get("type", "none")
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {auth['token']}"}
    if auth_type == "basic":
        return {"Authorization": f"Basic {_basic_token(auth['username'], auth['password'])}"}
    if auth_type == "apiKey":
        if auth.get("in", "header") != "header":
            return {}
        return {auth.get("name", "X-API-Key"): auth["value"]}
    if auth_type == "headers":
        return {str(k): str(v) for k, v in (auth.get("headers") or {}).items()}
    return {}


def auth_query_params(auth: dict[str, Any] | None) -> dict[str, str]:
    if not auth:
        return {}
    if auth.get("type") == "apiKey" and auth.get("in") == "query":
        return {auth.get("name", "api_key"): str(auth["value"])}
    return {}


def log_request(
    method: str,
    url: str,
    params: dict[str, Any],
    headers: dict[str, str],
    body: Any | None,
) -> None:
    request = httpx.Request(method, url, params=params)
    print(f"> {method} {redact_url(str(request.url))}", file=sys.stderr)
    if headers:
        print(f"> headers {json.dumps(redact_headers(headers), separators=(',', ':'))}", file=sys.stderr)
    if body is not None:
        print(f"> body {json.dumps(body, separators=(',', ':'))}", file=sys.stderr)


def log_response(response: httpx.Response, payload: Any) -> None:
    print(f"< {response.status_code} {redact_url(str(response.url))}", file=sys.stderr)
    interesting_headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() in {"content-type", "content-length", "etag", "link"}
    }
    if interesting_headers:
        print(f"< headers {json.dumps(redact_headers(interesting_headers), separators=(',', ':'))}", file=sys.stderr)
    print(f"< body {json.dumps(payload, separators=(',', ':'))}", file=sys.stderr)


def response_payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"_raw": response.text}


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: "[REDACTED]" if is_sensitive_name(key) else value
        for key, value in headers.items()
    }


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode(
        [
            (key, "[REDACTED]" if is_sensitive_name(key) else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def is_sensitive_name(name: str) -> bool:
    lower = name.lower()
    return any(marker in lower for marker in ("authorization", "token", "secret", "password", "api_key", "apikey", "key"))


def next_pagination_value(
    payload: Any,
    response: httpx.Response,
    pagination: dict[str, Any],
    params: dict[str, Any],
    page_count: int,
) -> tuple[str | None, Any]:
    """Return (next_url, next_value); both None means stop. next_url is only set for nextLink."""
    pagination_type = pagination.get("type", "none")
    if pagination_type == "none":
        return None, None

    if pagination_type == "cursor":
        cursor = json_select(payload, pagination["nextCursorPath"])
        if isinstance(cursor, list):
            raise SystemExit(
                f"nextCursorPath {pagination['nextCursorPath']!r} returned a list; "
                "check that the path targets a scalar cursor field"
            )
        return None, (cursor if cursor is not None else None)

    if pagination_type == "offset":
        items = json_select(payload, pagination.get("itemsPath", "$"))
        if not items:
            return None, None
        offset_param = pagination.get("offsetParam", "offset")
        limit_param = pagination.get("limitParam", "limit")
        page_size = int(params.get(limit_param) or pagination.get("limit") or 0)
        if not page_size:
            raise SystemExit(
                "offset pagination requires either the limit param in source.parameters "
                "or pagination.limit in config"
            )
        return None, int(params.get(offset_param, 0)) + page_size

    if pagination_type == "page":
        items = json_select(payload, pagination.get("itemsPath", "$"))
        if not items:
            return None, None
        page_param = pagination.get("pageParam", "page")
        return None, int(params.get(page_param, 1)) + 1

    if pagination_type == "nextLink":
        link_path = pagination.get("nextLinkPath")
        if link_path:
            nxt = json_select(payload, link_path) or None
        else:
            nxt = response.links.get("next", {}).get("url")
        return (str(nxt) if nxt else None), None

    raise SystemExit(f"Unsupported pagination.type: {pagination_type}")


def apply_next_pagination(params: dict[str, Any], pagination: dict[str, Any], next_value: Any) -> None:
    pagination_type = pagination.get("type", "none")
    if pagination_type == "cursor":
        params[pagination.get("cursorParam", "cursor")] = next_value
    elif pagination_type == "offset":
        params[pagination.get("offsetParam", "offset")] = next_value
    elif pagination_type == "page":
        params[pagination.get("pageParam", "page")] = next_value


def json_select(value: Any, path: str) -> Any:
    """Tiny JSONPath subset: $, $.field, $.field.0, $.items[*].id."""
    if path in {"", "$"}:
        return value
    if not path.startswith("$."):
        raise SystemExit(f"Unsupported JSONPath subset: {path}")

    current: Any = value
    for part in path[2:].split("."):
        if part.endswith("[*]"):
            key = part[:-3]
            current = current.get(key, []) if isinstance(current, dict) else []
            if not isinstance(current, list):
                return []
            continue

        if isinstance(current, list):
            if part.isdigit():
                index = int(part)
                current = current[index] if index < len(current) else None
            else:
                current = [item.get(part) for item in current if isinstance(item, dict)]
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def ensure_object_record(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"value": value}


def create_relay_session(client: httpx.Client, config: dict[str, Any], operation: Operation) -> str:
    bridge_url = config["destination"]["databridgeUrl"].rstrip("/")
    payload = {
        "source": {
            "type": "openapi",
            "baseUrl": config["source"].get("baseUrl"),
            "operationId": operation.operation_id,
            "method": operation.method,
            "path": operation.path,
        },
        "destination": {
            "sink": config["destination"]["sink"],
            "dataset": config["destination"]["dataset"],
            "mode": config["destination"].get("mode", "replace"),
        },
    }
    response = client.post(
        f"{bridge_url}/api/v1/client-relay/sessions",
        json=payload,
        headers=databridge_headers(config),
    )
    response.raise_for_status()
    body = response.json()
    session_id = body.get("id") or body.get("sessionId")
    if not session_id:
        raise SystemExit(f"backend returned no session ID: {body!r}")
    return str(session_id)


def upload_jsonl_chunk(
    client: httpx.Client,
    config: dict[str, Any],
    session_id: str,
    chunk_index: int,
    records: list[dict[str, Any]],
) -> None:
    bridge_url = config["destination"]["databridgeUrl"].rstrip("/")
    body = "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records)
    response = client.put(
        f"{bridge_url}/api/v1/client-relay/sessions/{session_id}/chunks/{chunk_index}",
        content=body.encode("utf-8"),
        headers={
            **databridge_headers(config),
            "Content-Type": "application/x-ndjson",
            "X-Chunk-Record-Count": str(len(records)),
        },
    )
    response.raise_for_status()


def complete_relay_session(
    client: httpx.Client,
    config: dict[str, Any],
    session_id: str,
    records: int,
) -> None:
    bridge_url = config["destination"]["databridgeUrl"].rstrip("/")
    response = client.post(
        f"{bridge_url}/api/v1/client-relay/sessions/{session_id}/complete",
        json={"records": records},
        headers=databridge_headers(config),
    )
    response.raise_for_status()


def abort_relay_session(
    client: httpx.Client,
    config: dict[str, Any],
    session_id: str,
) -> None:
    bridge_url = config["destination"]["databridgeUrl"].rstrip("/")
    try:
        client.post(
            f"{bridge_url}/api/v1/client-relay/sessions/{session_id}/abort",
            headers=databridge_headers(config),
        )
    except Exception:
        pass  # best-effort; must not mask the original exception


def databridge_headers(config: dict[str, Any]) -> dict[str, str]:
    auth = config.get("databridgeAuth") or {}
    if auth.get("type") == "bearer":
        return {"Authorization": f"Bearer {auth['token']}"}
    if auth.get("type") == "basic":
        return {"Authorization": f"Basic {_basic_token(auth['username'], auth['password'])}"}
    return {}


if __name__ == "__main__":
    raise SystemExit(main())
