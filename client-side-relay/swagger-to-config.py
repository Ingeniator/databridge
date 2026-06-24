#!/usr/bin/env python3
"""
Generate a databridge-relay YAML config from a Swagger / OpenAPI spec.

Usage:
  python3 swagger-to-config.py --spec http://service/openapi.json
  python3 swagger-to-config.py --spec openapi.json --operation listEvents -o relay.yaml
  python3 swagger-to-config.py --spec http://service/openapi.json --spec-token TOKEN
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx
import yaml


# ── Heuristic keyword sets ────────────────────────────────────────────────────

_CURSOR_RESPONSE_KEYS = frozenset({
    "cursor", "next_cursor", "next_token", "continuation_token",
    "page_token", "nextPageToken", "nextCursor", "next_page_token",
    "after", "nextAfter",
})
_NEXT_LINK_RESPONSE_KEYS = frozenset({
    "next", "next_url", "next_link", "nextLink",
    "@odata.nextLink", "nextHref", "nextPageUrl",
})
_OFFSET_PARAM_KEYS = frozenset({"offset", "skip"})
_LIMIT_PARAM_KEYS = frozenset({"limit", "per_page", "page_size", "pageSize", "max", "count", "size"})
_PAGE_PARAM_KEYS = frozenset({"page", "page_number", "pageNumber", "p"})
_CURSOR_PARAM_KEYS = frozenset({
    "cursor", "page_token", "continuation_token", "next_token", "after",
})
_RECORD_ARRAY_KEYS = frozenset({
    "items", "data", "results", "records", "events", "logs",
    "entries", "list", "content", "values", "rows", "elements",
    "hits", "objects", "members", "payload",
})
_FILTER_TIME_KEYS = frozenset({
    "from", "to", "start", "end", "since", "until",
    "start_time", "end_time", "startTime", "endTime",
    "after", "before", "date_from", "date_to",
    "from_date", "to_date", "fromTimestamp", "toTimestamp",
})


# ── Spec loading ──────────────────────────────────────────────────────────────

def load_spec(location: str, *, token: str | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if location.startswith(("http://", "https://")):
        r = httpx.get(location, headers=headers, timeout=30.0, follow_redirects=True)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return yaml.safe_load(r.text)
    with open(location, "r", encoding="utf-8") as fh:
        content = fh.read()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return yaml.safe_load(content)


# ── Schema helpers ────────────────────────────────────────────────────────────

def resolve_ref(spec: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        return {}
    current: Any = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(part, {})
        else:
            return {}
    return current if isinstance(current, dict) else {}


def deref(spec: dict[str, Any], schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    seen: set[str] = set()
    while "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen:
            break
        seen.add(ref)
        schema = resolve_ref(spec, ref)
    return schema


def collect_parameters(spec: dict[str, Any], op: dict[str, Any]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for p in op.get("_path_params", []) + (op["_op"].get("parameters") or []):
        p = deref(spec, p)
        key = (p.get("name", ""), p.get("in", ""))
        merged[key] = p
    return list(merged.values())


def get_response_schema(spec: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    responses = op["_op"].get("responses") or {}
    for code in ("200", "201", "default"):
        resp = responses.get(code)
        if not resp:
            continue
        resp = deref(spec, resp)
        for media_type in ("application/json", "*/*"):
            content = resp.get("content") or {}
            if media_type in content:
                return deref(spec, (content[media_type].get("schema") or {}))
        schema = resp.get("schema")
        if schema:
            return deref(spec, schema)
    return {}


def first_server_url(spec: dict[str, Any]) -> str | None:
    servers = spec.get("servers") or []
    if servers:
        return servers[0].get("url")
    host = spec.get("host")
    if host:
        scheme = (spec.get("schemes") or ["https"])[0]
        return f"{scheme}://{host}{spec.get('basePath', '')}"
    return None


# ── Operation discovery ───────────────────────────────────────────────────────

def list_operations(spec: dict[str, Any]) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        path_params = path_item.get("parameters") or []
        for method, raw_op in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(raw_op, dict):
                continue
            op_id = raw_op.get("operationId")
            if not op_id:
                continue
            ops.append({
                "method": method.upper(),
                "path": path,
                "operationId": op_id,
                "summary": raw_op.get("summary", ""),
                "tags": raw_op.get("tags", []),
                "_op": raw_op,
                "_path_params": path_params,
            })
    return ops


def pick_operation(ops: list[dict[str, Any]]) -> dict[str, Any]:
    print("\nAvailable operations:\n", file=sys.stderr)
    for i, op in enumerate(ops, 1):
        tags = f"[{', '.join(op['tags'])}] " if op["tags"] else ""
        summary = f" — {op['summary']}" if op["summary"] else ""
        print(
            f"  {i:3}.  {op['method']:<7} {op['path']:<45} "
            f"{tags}{op['operationId']}{summary}",
            file=sys.stderr,
        )
    print(file=sys.stderr)
    while True:
        try:
            raw = input("Select operation number: ").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(ops):
                return ops[idx]
        except (ValueError, EOFError):
            pass
        print("Invalid — enter a number from the list above.", file=sys.stderr)


# ── Inference ─────────────────────────────────────────────────────────────────

def infer_auth(spec: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    schemes: dict[str, Any] = (
        spec.get("components", {}).get("securitySchemes", {})
        or spec.get("securityDefinitions", {})
    )
    security: list[dict] = op["_op"].get("security") or spec.get("security") or []

    for req in security:
        for name in req:
            s = schemes.get(name, {})
            t = s.get("type", "").lower()
            if t == "http":
                if s.get("scheme", "").lower() == "bearer":
                    return {"type": "bearer", "token": "<TODO: bearer token>"}
                if s.get("scheme", "").lower() == "basic":
                    return {"type": "basic", "username": "<TODO>", "password": "<TODO>"}
            if t == "apikey":
                return {
                    "type": "apiKey",
                    "in": s.get("in", "header"),
                    "name": s.get("name", "X-API-Key"),
                    "value": "<TODO: API key>",
                }
            if t == "oauth2":
                return {"type": "bearer", "token": "<TODO: OAuth2 access token>"}
            if t == "basic":
                return {"type": "basic", "username": "<TODO>", "password": "<TODO>"}
    return {"type": "none"}


def infer_parameters(spec: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for p in collect_parameters(spec, op):
        name = p.get("name", "")
        loc = p.get("in", "")
        if loc not in {"query", "path"}:
            continue
        required = p.get("required", loc == "path")
        if required or name in _FILTER_TIME_KEYS:
            desc = p.get("description") or p.get("schema", {}).get("description") or name
            example = p.get("example") or p.get("schema", {}).get("example")
            params[name] = example if example is not None else f"<TODO: {desc}>"
    return params


def infer_pagination(
    spec: dict[str, Any],
    op: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """Return (pagination config, detected type label for user feedback)."""
    query_names = {
        p["name"]
        for p in collect_parameters(spec, op)
        if p.get("in") == "query" and p.get("name")
    }
    resp_schema = get_response_schema(spec, op)
    resp_props: set[str] = set()
    if resp_schema.get("type") == "object" or "properties" in resp_schema:
        resp_props = set((resp_schema.get("properties") or {}).keys())

    # nextLink wins — the URL is self-contained, clearest signal
    hit = resp_props & _NEXT_LINK_RESPONSE_KEYS
    if hit:
        field = next(iter(hit))
        return {"type": "nextLink", "nextLinkPath": f"$.{field}", "maxPages": 1000}, field

    # Cursor — response field or cursor-named query param
    cursor_resp = resp_props & _CURSOR_RESPONSE_KEYS
    cursor_param = query_names & _CURSOR_PARAM_KEYS
    if cursor_resp or cursor_param:
        resp_field = next(iter(cursor_resp), None)
        param_name = next(iter(cursor_param), "cursor")
        cfg: dict[str, Any] = {"type": "cursor", "cursorParam": param_name, "maxPages": 1000}
        cfg["nextCursorPath"] = f"$.{resp_field}" if resp_field else "<TODO: $.next_cursor>"
        return cfg, f"cursor via {resp_field or param_name!r}"

    # Offset — offset + limit params
    has_offset = query_names & _OFFSET_PARAM_KEYS
    has_limit = query_names & _LIMIT_PARAM_KEYS
    if has_offset and has_limit:
        limit_param = next(iter(has_limit))
        offset_param = next(iter(has_offset))
        return {
            "type": "offset",
            "offsetParam": offset_param,
            "limitParam": limit_param,
            "limit": 100,
            "maxPages": 1000,
        }, f"offset/{limit_param}"

    # Page number
    has_page = query_names & _PAGE_PARAM_KEYS
    if has_page and has_limit:
        page_param = next(iter(has_page))
        limit_param = next(iter(has_limit))
        return {
            "type": "page",
            "pageParam": page_param,
            "maxPages": 1000,
        }, f"page via {page_param!r}"

    return {"type": "none"}, None


def infer_record_path(spec: dict[str, Any], op: dict[str, Any]) -> str:
    schema = get_response_schema(spec, op)
    if not schema:
        return "$"
    if schema.get("type") == "array":
        return "$"
    props: dict[str, Any] = schema.get("properties") or {}
    # Prefer known names
    for name in _RECORD_ARRAY_KEYS:
        if name in props:
            prop = deref(spec, props[name])
            if prop.get("type") == "array":
                return f"$.{name}"
    # Fall back to first array property found
    for name, prop in props.items():
        prop = deref(spec, prop)
        if prop.get("type") == "array":
            return f"$.{name}"
    return "$"


# ── Config assembly ───────────────────────────────────────────────────────────

def build_config(
    spec: dict[str, Any],
    op: dict[str, Any],
    base_url: str,
    *,
    dataset: str | None = None,
) -> dict[str, Any]:
    auth = infer_auth(spec, op)
    parameters = infer_parameters(spec, op)
    pagination, pag_label = infer_pagination(spec, op)
    record_path = infer_record_path(spec, op)

    # Seed pagination params into parameters so the first request is valid
    pag_type = pagination.get("type")
    if pag_type == "offset":
        parameters.setdefault(pagination.get("limitParam", "limit"), pagination.get("limit", 100))
        parameters.setdefault(pagination.get("offsetParam", "offset"), 0)
    elif pag_type == "page":
        parameters.setdefault(pagination.get("pageParam", "page"), 1)

    print(f"\n  operation : {op['method']} {op['path']}", file=sys.stderr)
    print(f"  base URL  : {base_url}", file=sys.stderr)
    print(f"  auth      : {auth.get('type')}", file=sys.stderr)
    print(f"  pagination: {pag_type}" + (f" ({pag_label})" if pag_label else ""), file=sys.stderr)
    print(f"  records at: {record_path}", file=sys.stderr)
    print(f"  params    : {list(parameters)}\n", file=sys.stderr)

    source: dict[str, Any] = {
        "type": "openapi",
        "spec": "<TODO: URL or local path to the spec>",
        "operationId": op["operationId"],
        "baseUrl": base_url,
    }
    if auth.get("type") != "none":
        source["auth"] = auth
    if parameters:
        source["parameters"] = parameters
    source["pagination"] = pagination
    source["output"] = {"recordPath": record_path, "format": "jsonl"}

    return {
        "source": source,
        "destination": {
            "databridgeUrl": "<TODO: Databridge backend URL>",
            "databridgeAuth": {"type": "bearer", "token": "<TODO: Databridge token>"},
            "sink": "<TODO: sink name>",
            "dataset": dataset or op["operationId"].lower().replace(" ", "_"),
            "mode": "replace",
        },
        "transfer": {"chunkRecords": 500},
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="swagger-to-config",
        description="Generate a databridge-relay YAML config from an OpenAPI/Swagger spec.",
    )
    parser.add_argument("--spec", required=True, help="URL or local path to openapi.json / swagger.json")
    parser.add_argument("--spec-token", metavar="TOKEN", help="Bearer token for fetching a protected spec")
    parser.add_argument("--operation", help="operationId to use (skips interactive selection)")
    parser.add_argument("--base-url", help="Override the base URL inferred from the spec")
    parser.add_argument("--dataset", help="Dataset name in the destination (default: operationId)")
    parser.add_argument("-o", "--output", metavar="FILE", help="Write YAML to this file (default: stdout)")
    args = parser.parse_args()

    print(f"Loading spec from {args.spec} …", file=sys.stderr)
    spec = load_spec(args.spec, token=args.spec_token)
    title = spec.get("info", {}).get("title", "")
    version = spec.get("info", {}).get("version", "")
    if title:
        print(f"  {title} {version}", file=sys.stderr)

    all_ops = list_operations(spec)
    if not all_ops:
        raise SystemExit("No operations with operationId found in spec.")
    print(f"  {len(all_ops)} operations found", file=sys.stderr)

    if args.operation:
        op = next((o for o in all_ops if o["operationId"] == args.operation), None)
        if op is None:
            ids = [o["operationId"] for o in all_ops]
            raise SystemExit(f"operationId {args.operation!r} not found.\nAvailable: {ids}")
    else:
        op = pick_operation(all_ops)

    base_url = args.base_url or first_server_url(spec) or "<TODO: service base URL>"
    config = build_config(spec, op, base_url, dataset=args.dataset)
    output = yaml.dump(config, sort_keys=False, allow_unicode=True, default_flow_style=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(output)
        print(f"Config written to {args.output}", file=sys.stderr)
        print(f"Review TODO markers, then test with:", file=sys.stderr)
        print(f"  python3 databridge-relay.py openapi --config {args.output} --dry-run --limit 5 --verbose", file=sys.stderr)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
