from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml


SCRIPT_PATH = Path(__file__).parents[1] / "databridge-relay.py"
SPEC = importlib.util.spec_from_file_location("databridge_relay", SCRIPT_PATH)
relay = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = relay
SPEC.loader.exec_module(relay)


def test_find_operation_supports_openapi_paths():
    spec = {
        "paths": {
            "/events": {
                "get": {"operationId": "listEvents"},
                "post": {"operationId": "createEvent"},
            }
        }
    }

    operation = relay.find_operation(spec, "listEvents")

    assert operation.method == "GET"
    assert operation.path == "/events"
    assert operation.operation_id == "listEvents"


def test_auth_headers_and_query_params():
    assert relay.auth_headers({"type": "bearer", "token": "abc"}) == {
        "Authorization": "Bearer abc"
    }
    assert relay.auth_headers({"type": "apiKey", "in": "header", "name": "X-Key", "value": "secret"}) == {
        "X-Key": "secret"
    }
    assert relay.auth_query_params({"type": "apiKey", "in": "query", "name": "api_key", "value": "secret"}) == {
        "api_key": "secret"
    }


def test_redacts_sensitive_verbose_values():
    assert relay.redact_headers({"Authorization": "Bearer abc", "Content-Type": "application/json"}) == {
        "Authorization": "[REDACTED]",
        "Content-Type": "application/json",
    }
    assert relay.redact_url("http://api.local/events?api_key=secret&limit=2") == (
        "http://api.local/events?api_key=%5BREDACTED%5D&limit=2"
    )


def test_json_select_subset():
    payload = {
        "items": [
            {"id": 1, "name": "a"},
            {"id": 2, "name": "b"},
        ],
        "meta": {"next": "cursor-2"},
    }

    assert relay.json_select(payload, "$.items.0.id") == 1
    assert relay.json_select(payload, "$.items[*].name") == ["a", "b"]
    assert relay.json_select(payload, "$.meta.next") == "cursor-2"


def test_iter_openapi_records_with_cursor_pagination():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        cursor = request.url.params.get("cursor")
        if cursor == "cursor-2":
            return httpx.Response(
                200,
                json={
                    "items": [{"id": 3}],
                    "next_cursor": None,
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [{"id": 1}, {"id": 2}],
                "next_cursor": "cursor-2",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = {
        "parameters": {"limit": 2},
        "pagination": {
            "type": "cursor",
            "cursorParam": "cursor",
            "nextCursorPath": "$.next_cursor",
            "itemsPath": "$.items",
        },
    }
    operation = relay.Operation(method="GET", path="/events", operation_id="listEvents")

    records = list(relay.iter_openapi_records(client, "http://api.local", source, operation, "$.items"))

    assert records == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert len(requests) == 2
    assert requests[0].url.params["limit"] == "2"
    assert requests[1].url.params["cursor"] == "cursor-2"


def test_upload_jsonl_chunk_posts_expected_payload():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers["content-type"]
        captured["record_count"] = request.headers["x-chunk-record-count"]
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    config = {"destination": {"databridgeUrl": "http://databridge.local"}}

    relay.upload_jsonl_chunk(client, config, "session-1", 2, [{"id": 1}, {"id": 2}])

    assert captured == {
        "url": "http://databridge.local/api/v1/client-relay/sessions/session-1/chunks/2",
        "content_type": "application/x-ndjson",
        "record_count": "2",
        "body": '{"id":1}\n{"id":2}\n',
    }


def test_relay_dry_run_against_mock_openapi_service(tmp_path, capsys):
    port = free_port()
    env = {
        **os.environ,
        "PORT": str(port),
        "MOCK_API_TOKEN": "relay-token",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("mock-openapi-service.py"))],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        wait_for_health(port)
        config_path = tmp_path / "relay.openapi.mock.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "source": {
                        "type": "openapi",
                        "spec": f"http://localhost:{port}/openapi.json",
                        "baseUrl": f"http://localhost:{port}",
                        "operationId": "listEvents",
                        "auth": {"type": "bearer", "token": "relay-token"},
                        "parameters": {"limit": 2},
                        "pagination": {
                            "type": "cursor",
                            "cursorParam": "cursor",
                            "nextCursorPath": "$.next_cursor",
                            "itemsPath": "$.items",
                        },
                        "output": {"format": "jsonl", "recordPath": "$.items"},
                    },
                    "destination": {
                        "databridgeUrl": "http://localhost:5010",
                        "sink": "dataset-mock",
                        "dataset": "mock-openapi-events",
                        "mode": "replace",
                    },
                    "transfer": {"chunkRecords": 2},
                }
            ),
            encoding="utf-8",
        )

        assert relay.run_openapi(str(config_path), dry_run=True, limit=None, verbose=True) == 0

        captured = capsys.readouterr()
        assert '{"id":"evt-001","kind":"trace","message":"first event"}' in captured.out
        assert '{"id":"evt-005","kind":"log","message":"fifth event"}' in captured.out
        assert f"> GET http://localhost:{port}/events?limit=2" in captured.err
        assert f"< 200 http://localhost:{port}/events?limit=2" in captured.err
        assert "> headers {\"Authorization\":\"[REDACTED]\"}" in captured.err
        assert "< body {\"items\":[{\"id\":\"evt-001\"" in captured.err
        assert f"> GET http://localhost:{port}/events?limit=2&cursor=2" in captured.err
        assert "read chunk 0: 2 records" in captured.err
        assert "read chunk 1: 2 records" in captured.err
        assert "read chunk 2: 1 records" in captured.err
        assert "read 5 records" in captured.err
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(port: int) -> None:
    deadline = time.monotonic() + 10
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"http://localhost:{port}/health", timeout=0.5)
            if response.status_code == 200:
                return
        except Exception as exc:  # pragma: no cover - diagnostic only
            last_error = exc
        time.sleep(0.1)
    raise AssertionError(f"mock OpenAPI service did not become healthy: {last_error}")
