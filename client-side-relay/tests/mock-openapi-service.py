#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


PORT = int(os.environ.get("PORT", "8080"))
TOKEN = os.environ.get("MOCK_API_TOKEN", "relay-token")

EVENTS = [
    {"id": "evt-001", "kind": "trace", "message": "first event"},
    {"id": "evt-002", "kind": "trace", "message": "second event"},
    {"id": "evt-003", "kind": "metric", "message": "third event"},
    {"id": "evt-004", "kind": "metric", "message": "fourth event"},
    {"id": "evt-005", "kind": "log", "message": "fifth event"},
]


def openapi_spec(base_url: str) -> dict:
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Databridge Relay Mock API",
            "version": "1.0.0",
        },
        "servers": [{"url": base_url}],
        "paths": {
            "/events": {
                "get": {
                    "operationId": "listEvents",
                    "summary": "List events with cursor pagination",
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                        {
                            "name": "cursor",
                            "in": "query",
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "A page of events",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "items": {
                                                "type": "array",
                                                "items": {"$ref": "#/components/schemas/Event"},
                                            },
                                            "next_cursor": {"type": "string", "nullable": True},
                                        },
                                    }
                                }
                            },
                        },
                        "401": {"description": "Missing or invalid token"},
                    },
                }
            }
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                }
            },
            "schemas": {
                "Event": {
                    "type": "object",
                    "required": ["id", "kind", "message"],
                    "properties": {
                        "id": {"type": "string"},
                        "kind": {"type": "string"},
                        "message": {"type": "string"},
                    },
                }
            },
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "DatabridgeRelayMock/1.0"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json({"status": "ok"})
            return

        if parsed.path in {"/openapi.json", "/swagger.json"}:
            base_url = f"http://{self.headers.get('Host', f'localhost:{PORT}')}"
            self.send_json(openapi_spec(base_url))
            return

        if parsed.path == "/events":
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self.send_json({"error": "unauthorized"}, status=401)
                return
            self.send_json(self.events_page(parse_qs(parsed.query)))
            return

        self.send_json({"error": "not found"}, status=404)

    def events_page(self, query: dict[str, list[str]]) -> dict:
        limit = int((query.get("limit") or ["2"])[0])
        cursor = (query.get("cursor") or ["0"])[0]
        offset = int(cursor) if cursor.isdigit() else 0
        items = EVENTS[offset : offset + limit]
        next_offset = offset + len(items)
        next_cursor = str(next_offset) if next_offset < len(EVENTS) else None
        return {
            "items": items,
            "next_cursor": next_cursor,
        }

    def send_json(self, payload: dict, *, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Type")

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"mock OpenAPI service listening on 0.0.0.0:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
