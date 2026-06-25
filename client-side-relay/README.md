# Client-Side Relay Prototype

This folder contains a standalone relay prototype for sources that are reachable from a user's workstation but not from the Databridge backend.

The first implemented scenario is OpenAPI relay:

```text
Custom API LAN -> local relay process -> Databridge backend LAN
```

The script reads records from a selected OpenAPI operation, converts them to JSONL chunks, and uploads those chunks to the proposed Databridge client-relay session API.

## Run

Dry-run against the source API without uploading:

```bash
python databridge-relay.py openapi --config relay.openapi.yaml --dry-run --limit 100
```

Print each extracted record as compact JSON while running:

```bash
python databridge-relay.py openapi --config relay.openapi.yaml --dry-run --verbose
```

Verbose mode writes transferred records to stdout and request/response diagnostics to stderr. Authorization-like headers and sensitive query parameters are redacted:

```text
> GET http://localhost:8095/events?limit=2
> headers {"Authorization":"[REDACTED]"}
< 200 http://localhost:8095/events?limit=2
< headers {"content-type":"application/json","content-length":"156"}
< body {"items":[...],"next_cursor":"2"}
{"id":"evt-001","kind":"trace","message":"first event"}
```

Relay into Databridge:

```bash
python databridge-relay.py openapi --config relay.openapi.yaml
```

## Mock OpenAPI Service

This folder includes a mock OpenAPI service for local relay testing. It exposes:

| Endpoint | Description |
|---|---|
| `/health` | Health check |
| `/openapi.json` | OpenAPI 3 spec |
| `/swagger.json` | Alias for the same spec |
| `/events` | Bearer-authenticated cursor-paginated event API |

Start it with Docker Compose:

```bash
docker compose -f client-side-relay/docker-compose.yml up --build
```

From another terminal, run the relay in dry-run mode:

```bash
python client-side-relay/databridge-relay.py openapi \
  --config client-side-relay/tests/relay.openapi.mock.yaml \
  --dry-run
```

To inspect the transferred records:

```bash
python client-side-relay/databridge-relay.py openapi \
  --config client-side-relay/tests/relay.openapi.mock.yaml \
  --dry-run \
  --verbose
```

Expected result:

```text
read chunk 0: 2 records
read chunk 1: 2 records
read chunk 2: 1 records
read 5 records
```

The mock API requires this source auth:

```yaml
auth:
  type: bearer
  token: relay-token
```

## Generating a Config from a Swagger Spec

`swagger-to-config.py` reads an OpenAPI / Swagger spec and generates a ready-to-edit relay config. It infers auth type, pagination strategy, and record path from the spec so you only need to fill in credential values and destination details.

Interactive — lists all operations, you pick by number:

```bash
python swagger-to-config.py --spec http://service-a.lan:8080/openapi.json
```

Non-interactive — jump straight to an operation and write the file:

```bash
python swagger-to-config.py --spec openapi.json --operation listEvents -o relay.yaml
```

If the spec itself requires auth to fetch:

```bash
python swagger-to-config.py --spec http://service/openapi.json --spec-token $TOKEN -o relay.yaml
```

What the script infers automatically:

| Thing | How |
|---|---|
| Auth type | reads `securitySchemes` / `securityDefinitions`, maps to bearer / basic / apiKey |
| Pagination | heuristic on query param names (`offset`, `cursor`, `page`) and response field names (`next_cursor`, `next`, `@odata.nextLink`, etc.) |
| Record path | walks the 200-response schema, finds the first array property with a known name (`items`, `data`, `results`, …) |
| Required params | any param marked `required: true` or matching common time-filter names |

Credential values, Databridge URL, and sink name are left as `<TODO:>` markers for you to fill in.

After generating, validate with a dry-run before touching the backend:

```bash
python databridge-relay.py openapi --config relay.yaml --dry-run --limit 5 --verbose
```

## OpenAPI Config

```yaml
source:
  type: openapi
  spec: http://service-a.lan:8080/openapi.json
  baseUrl: http://service-a.lan:8080
  operationId: listEvents
  auth:
    type: bearer
    token: token-value
  parameters:
    from: "2026-06-01T00:00:00Z"
    to: "2026-06-23T00:00:00Z"
    limit: 1000
  pagination:
    type: cursor
    cursorParam: cursor
    nextCursorPath: $.next_cursor
    itemsPath: $.items
  output:
    format: jsonl
    recordPath: $.items

destination:
  databridgeUrl: http://localhost:5010
  sink: dataset-mock
  dataset: events
  mode: replace

transfer:
  chunkRecords: 1000
```

Supported auth modes:

| Type | Shape |
|---|---|
| Bearer | `{type: bearer, token: ...}` |
| Basic | `{type: basic, username: ..., password: ...}` |
| API key header | `{type: apiKey, in: header, name: X-API-Key, value: ...}` |
| API key query | `{type: apiKey, in: query, name: api_key, value: ...}` |
| Custom headers | `{type: headers, headers: {X-Token: ...}}` |

Supported pagination modes:

| Type | Description |
|---|---|
| `none` | Single request |
| `cursor` | Reads a cursor from the response and sends it as a query parameter |
| `offset` | Increments an offset parameter by the limit |
| `page` | Increments a page number parameter |
| `nextLink` | Reads the next URL from the response body or HTTP `Link` header |

The JSONPath implementation is intentionally small. It supports selectors like `$`, `$.items`, `$.items.0`, and `$.items[*].id`.

## Backend Assumption

Non-dry-run mode targets these proposed endpoints:

```text
POST /api/v1/client-relay/sessions
PUT  /api/v1/client-relay/sessions/{id}/chunks/{index}
POST /api/v1/client-relay/sessions/{id}/complete
```

Until those endpoints exist, use `--dry-run` for source-side testing.

## Tests

Run from the Databridge repo root:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest client-side-relay/test_databridge_relay.py
```
