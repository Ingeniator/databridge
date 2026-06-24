# Client-Side Relay Mode

Client-side relay mode lets a user move data between two isolated network segments when the Databridge backend cannot reach the datasource directly, but the user's workstation can reach both sides.

Instead of routing bytes through a server-side adapter or worker, the browser becomes the temporary network bridge:

```text
Datasource LAN -> user's browser -> Databridge backend LAN
```

The primary source for this mode is an S3-compatible bucket or prefix reachable from the user's browser. Generic HTTP source URLs can be supported later, but bucket/prefix relay is the expected product path.

## Use case

Example scenario:

- An S3-compatible datasource is available only from LAN segment A.
- The Databridge backend is available only from LAN segment B.
- LAN A and LAN B are not connected to each other.
- The user opens Databridge from a machine that can access both LAN A and LAN B.
- The user enters S3 bucket credentials and a prefix.
- The user clicks "Start relay transfer" and the browser lists objects, streams them from S3, and uploads them into Databridge.

This allows a controlled one-time transfer without opening direct backend-to-datasource connectivity.

## User inputs

The user should provide S3-compatible source credentials and object selection rules, plus the Databridge destination where the relayed objects should land.

Minimal UI fields:

| Field | Description |
|---|---|
| S3 endpoint | S3 or S3-compatible endpoint reachable from the user's browser, for example MinIO |
| Region | S3 region; use `us-east-1` for many local S3-compatible services |
| Bucket | Source bucket name |
| Prefix | Optional object prefix to relay |
| Access key ID | Source S3 access key entered for this browser session |
| Secret access key | Source S3 secret key entered for this browser session |
| Addressing style | Virtual-hosted or path-style addressing |
| Format | Auto, CSV, JSON, JSONL, Parquet, ZIP, or binary |
| Destination sink | Existing Databridge datasink name |
| Destination dataset | Dataset, folder, table, or object name inside the sink |
| Import mode | New, replace, or append, depending on sink capability |

Advanced fields:

| Field | Description |
|---|---|
| Include patterns | Optional glob-style filters, for example `*.jsonl` or `events/**/*.parquet` |
| Exclude patterns | Optional glob-style filters for skipped objects |
| Max objects | Optional safety limit for object count |
| Max bytes | Optional safety limit for total transfer size |
| Object concurrency | Number of source objects fetched concurrently |
| Chunk size | Upload chunk size used by the browser-to-backend stream |
| Checksum mode | Optional source ETag, SHA-256, or backend-calculated digest verification |
| Resume token | Optional token for continuing a previously interrupted transfer |

Example source descriptor:

```json
{
  "source": {
    "type": "s3",
    "endpoint": "http://minio.source-lan:9000",
    "region": "us-east-1",
    "bucket": "raw-events",
    "prefix": "2026/06/",
    "accessKeyId": "<entered-in-browser>",
    "secretAccessKey": "<entered-in-browser>",
    "addressingStyle": "path",
    "include": ["*.jsonl", "*.parquet"],
    "exclude": [],
    "format": "auto"
  },
  "destination": {
    "sink": "dataset-mock",
    "dataset": "raw-events",
    "mode": "replace"
  },
  "transfer": {
    "chunkSizeBytes": 8388608,
    "objectConcurrency": 2,
    "maxObjects": null,
    "maxBytes": null,
    "checksum": null
  }
}
```

Source credentials should normally be session-only. The Databridge backend does not need to store credentials for a network it cannot reach.

## Flow

1. User opens Databridge in a browser that can reach both networks.
2. User selects "Client-side relay" as the import mode.
3. User enters the S3 endpoint, bucket, prefix, credentials, format, and destination.
4. Browser runs a source access test:
   - DNS and TCP reachability from the user's machine.
   - S3 authentication by calling `ListObjectsV2`.
   - CORS compatibility for list and get-object requests.
   - Object count and total size estimate from S3 metadata.
5. Databridge creates a relay upload session and returns an upload session ID.
6. Browser lists source objects under the selected bucket/prefix.
7. Browser fetches each selected S3 object as a stream.
8. Browser uploads object chunks to Databridge backend.
9. Backend writes chunks to the selected sink or to a staging area.
10. Backend verifies object size/checksum when available.
11. Backend finalises the dataset and records transfer metadata.

## API shape

The backend should treat client relay uploads as upload sessions, not normal server-side export jobs.

Possible endpoints:

| Endpoint | Responsibility |
|---|---|
| `POST /api/v1/client-relay/sessions` | Create a relay upload session with destination metadata |
| `PUT /api/v1/client-relay/sessions/{id}/chunks/{index}` | Receive one chunk |
| `POST /api/v1/client-relay/sessions/{id}/complete` | Finalise the upload |
| `POST /api/v1/client-relay/sessions/{id}/abort` | Cancel and clean up staging data |
| `GET /api/v1/client-relay/sessions/{id}` | Return status, uploaded bytes, errors, and final asset info |

The source descriptor and source credentials can remain browser-local. The backend only needs destination metadata, object manifests, transfer limits, integrity metadata, and audit fields.

The session creation request can include a planned object manifest without credentials:

```json
{
  "source": {
    "type": "s3",
    "endpointHost": "minio.source-lan:9000",
    "bucket": "raw-events",
    "prefix": "2026/06/"
  },
  "objects": [
    {
      "key": "2026/06/events-0001.jsonl",
      "size": 104857600,
      "etag": "\"9b2cf535f27731c974343645a3985328\""
    }
  ],
  "destination": {
    "sink": "dataset-mock",
    "dataset": "raw-events",
    "mode": "replace"
  }
}
```

## Browser implementation notes

Use AWS SDK for JavaScript v3 as the browser-side S3 client:

- `S3Client`
- `ListObjectsV2Command`
- `GetObjectCommand`
- optional ranged `GetObjectCommand` calls for resume support

The browser should avoid reading whole objects into memory. Prefer streaming APIs:

- AWS SDK `GetObjectCommand` to obtain the object body.
- `ReadableStream` reader or SDK-compatible stream handling to process chunks.
- `fetch(uploadUrl, { method: "PUT", body: chunk })` for each backend upload chunk.
- `AbortController` for cancellation.
- Progress based on uploaded bytes and S3 object sizes from listing/head metadata.

For resumability, the backend can expose which object/chunk indexes were received. The browser can restart from the first missing chunk by using ranged `GetObject` requests when the S3-compatible service supports them.

This mode should relay raw objects or object chunks. It should not try to reproduce the full server-side S3 adapter behaviour in the browser, such as DuckDB scans, SQL filtering, schema inference across mixed formats, or server-side sampling.

## Constraints

### Browser reachability

The user's browser must be able to reach both:

- The datasource endpoint on the source LAN.
- The Databridge backend on the destination LAN.

If the user has access to both networks but not at the same time, client-side relay cannot be fully automatic.

### CORS

Direct browser S3 calls require the bucket or S3-compatible service to allow cross-origin requests from the Databridge UI origin. The browser needs CORS permission for S3 API calls such as `ListBucket`, `GetObject`, and any headers used by SigV4 signing.

Example S3/MinIO CORS policy shape:

```json
[
  {
    "AllowedOrigins": ["https://databridge.example.internal"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag", "Content-Length", "Content-Type"],
    "MaxAgeSeconds": 3000
  }
]
```

If CORS is not enabled, the browser may be able to navigate to or download an object URL but still be blocked from listing or streaming it through JavaScript.

Fallback options:

- Configure CORS on the source S3 bucket or S3-compatible service.
- Ask the user to download the file and upload it manually.
- Provide a local helper application for environments where browser CORS cannot be changed.

### Credentials

Source S3 credentials should be entered into the browser session and used only by the browser-side AWS SDK client. They should not be persisted in the Databridge connection registry unless there is a separate reason to store them.

Temporary credentials are preferable when available. For AWS S3, use short-lived STS credentials if the environment can provide them. For MinIO or other S3-compatible systems, use a scoped access key with read-only access to the selected bucket/prefix.

Destination permissions still use normal Databridge authentication and authorization.

### Large files

Large transfers need:

- Chunked upload.
- Retry per chunk.
- Transfer progress.
- Cancellation.
- Optional resume.
- Backend-side size limits.
- Checksum or digest verification.

The UI should show that the user's browser tab and workstation are part of the data path. Closing the tab or losing network connectivity may interrupt the transfer unless resumability is implemented.

## Security and audit

Client-side relay changes the trust boundary. Data passes through the user's workstation and browser process before reaching Databridge.

The feature should record:

- User identity.
- Destination sink and dataset.
- Source endpoint host, bucket, prefix, and object keys, with secrets redacted.
- Start time, finish time, status, and byte count.
- Source content type, object count, and object sizes, if available.
- Checksum result, if available.
- Browser-reported errors, with credentials redacted.

The feature should not log:

- Authorization headers.
- Cookies.
- Access key secrets.
- Session tokens.
- Full presigned URLs, unless sensitive query parameters are stripped.

## Relationship to existing export jobs

Existing export jobs are server-side:

```text
Databridge worker -> datasource -> sink
```

Client-side relay is browser-side:

```text
Browser -> datasource
Browser -> Databridge upload session -> sink
```

It should be presented as a separate import path, not as a replacement for normal datasource connections. Server-side exports remain preferable when the backend can reach the datasource because they are easier to schedule, monitor, retry, query, sample, mask, and run without keeping the user's browser open.

Client-side relay is best understood as S3 bucket/prefix/object relay. It moves selected source objects into a Databridge sink; it is not the same as a server-side Databridge export from S3 through DuckDB.

## OpenAPI scenario relay

A second use case is a user who can reach a custom internal service with an OpenAPI/Swagger description, while the Databridge backend cannot reach that service directly.

In this mode, the browser executes a declarative API scenario:

```text
Custom API LAN -> user's browser -> Databridge backend LAN
```

The user provides:

| Field | Description |
|---|---|
| OpenAPI spec | URL or uploaded `openapi.json` / `swagger.json` file |
| Base URL | Service base URL reachable from the user's browser |
| Operation | Selected `operationId`, method, and path from the spec |
| Auth | None, API key, Bearer token, or Basic auth entered for this browser session |
| Parameters | Path, query, and header parameters required by the operation |
| Request body | JSON body for `POST` or `PUT` operations |
| Pagination | Offset, page, cursor, or next-link configuration |
| Record path | JSONPath-like selector for records inside each response |
| Destination sink | Existing Databridge datasink name |
| Destination dataset | Dataset, folder, table, or object name inside the sink |

Example scenario descriptor:

```json
{
  "source": {
    "type": "openapi",
    "specUrl": "http://service-a.lan:8080/openapi.json",
    "baseUrl": "http://service-a.lan:8080",
    "operationId": "listEvents",
    "auth": {
      "type": "bearer",
      "token": "<entered-in-browser>"
    },
    "parameters": {
      "from": "2026-06-01T00:00:00Z",
      "to": "2026-06-23T00:00:00Z"
    },
    "pagination": {
      "type": "cursor",
      "cursorParam": "cursor",
      "nextCursorPath": "$.next_cursor",
      "itemsPath": "$.items"
    },
    "output": {
      "format": "jsonl",
      "recordPath": "$.items"
    }
  },
  "destination": {
    "sink": "dataset-mock",
    "dataset": "events",
    "mode": "replace"
  }
}
```

Recommended MVP scope:

| Area | Supported first |
|---|---|
| Specs | OpenAPI 3 JSON, Swagger 2 JSON if easy to normalize |
| Methods | `GET` and `POST` |
| Auth | None, API key header/query, Bearer token, Basic auth |
| Responses | JSON responses |
| Pagination | Offset, page number, cursor token, next-link |
| Extraction | JSONPath-like record selector |
| Output | JSONL chunks uploaded to a relay session |

The scenario should be declarative, not arbitrary JavaScript. Databridge can render forms from the OpenAPI schema, let the user test one request, then execute the configured loop in the browser:

1. Load or parse the OpenAPI spec.
2. Let the user choose an operation.
3. Render required auth, parameter, and body inputs.
4. Run a browser-side test request.
5. Extract records from the response using the configured record path.
6. Follow pagination until complete, cancelled, or limited.
7. Upload JSONL chunks to Databridge.
8. Finalise the destination dataset.

OpenAPI scenario relay has the same network and browser constraints as S3 relay. The custom service must be reachable from the user's browser and must allow CORS from the Databridge UI origin for the selected API requests.

Do not try to make the first version support every possible Swagger API. Defer:

- OAuth browser flows.
- WebSockets and streaming APIs.
- Multipart uploads.
- Binary downloads.
- Multi-step workflows with conditionals.
- Arbitrary user-provided JavaScript.
- APIs that require cookies or browser navigation side effects.

## Local relay SDK or CLI

A local relay SDK or CLI is the stronger option when browser relay is blocked by CORS, large transfer size, service auth complexity, or the need to run unattended.

The data path changes from browser JavaScript to a local process on the user's machine:

```text
Source LAN -> local relay process -> Databridge backend LAN
```

The user still needs a machine that can reach both sides, but the transfer no longer depends on browser cross-origin rules or an open browser tab.

Benefits:

| Area | Browser relay | Local SDK/CLI relay |
|---|---|---|
| Install required | No | Yes |
| CORS required | Yes | No |
| Large transfers | Fragile unless carefully implemented | Better streaming, retry, and resume |
| Unattended runs | Poor fit | Good fit |
| S3 support | Browser AWS SDK + CORS | Normal AWS SDK / MinIO SDK |
| OpenAPI support | Browser `fetch` + CORS | Normal HTTP client with fewer browser limits |
| Local files | User-selected files only | Direct filesystem access with user permissions |
| Trust model | Code runs in browser session | User runs a signed local tool |

Example S3 command:

```bash
databridge-relay s3 \
  --endpoint http://minio.source-lan:9000 \
  --region us-east-1 \
  --bucket raw-events \
  --prefix 2026/06/ \
  --addressing-style path \
  --dest https://databridge.segment-b.local \
  --sink dataset-mock \
  --dataset raw-events
```

Example OpenAPI command:

```bash
databridge-relay openapi \
  --spec http://service-a.lan:8080/openapi.json \
  --base-url http://service-a.lan:8080 \
  --operation listEvents \
  --params params.json \
  --record-path '$.items' \
  --dest https://databridge.segment-b.local \
  --sink dataset-mock \
  --dataset events
```

The Databridge UI can still provide a good user experience by generating a relay config:

```yaml
source:
  type: s3
  endpoint: http://minio.source-lan:9000
  region: us-east-1
  bucket: raw-events
  prefix: 2026/06/
  addressing_style: path

destination:
  databridge_url: https://databridge.segment-b.local
  sink: dataset-mock
  dataset: raw-events
  mode: replace

transfer:
  chunk_size_bytes: 8388608
  object_concurrency: 4
```

Then the user runs:

```bash
databridge-relay run relay.yaml
```

Recommended product positioning:

- Browser relay is the no-install path for CORS-compatible S3 buckets and simple APIs.
- Local SDK/CLI relay is the recommended production path for large transfers, custom OpenAPI services, non-CORS sources, and unattended jobs.
- Both paths should use the same backend upload-session API so monitoring, audit, destination handling, and finalisation stay consistent.

## Recommended UX

Suggested S3 relay UI:

```text
Import data

Mode:
[ Server-side datasource ] [ S3 relay ] [ OpenAPI scenario relay ] [ Local relay CLI ]

S3 endpoint:
[ http://minio.source-lan:9000 ]

Bucket:
[ raw-events ]

Prefix:
[ 2026/06/ ]

Credentials:
[ Access key ID ] [ Secret access key ]

Addressing:
[ Path-style | Virtual-hosted ]

Objects:
[ Include patterns ] [ Exclude patterns ]

Format:
[ Auto | CSV | JSON | JSONL | Parquet | ZIP | Binary ]

Destination:
[ sink ] [ dataset ] [ mode ]

[ Test Source Access ] [ Start Streaming ]
```

`Test Source Access` should run before transfer and clearly report whether the failure is caused by reachability, S3 authentication, CORS, bucket permissions, unsupported addressing style, or an empty object selection.

Suggested OpenAPI relay UI:

```text
Import data

Mode:
[ Server-side datasource ] [ S3 relay ] [ OpenAPI scenario relay ] [ Local relay CLI ]

OpenAPI spec:
[ http://service-a.lan:8080/openapi.json ] [ Upload spec ]

Base URL:
[ http://service-a.lan:8080 ]

Operation:
[ GET /events - listEvents ]

Auth:
[ None | API key | Bearer | Basic ]

Parameters:
[ from ] [ to ] [ limit ]

Pagination:
[ None | Offset | Page | Cursor | Next link ]

Record path:
[ $.items ]

Destination:
[ sink ] [ dataset ] [ mode ]

[ Test Request ] [ Start Scenario ]
```

Suggested local relay CLI UI:

```text
Import data

Mode:
[ Server-side datasource ] [ S3 relay ] [ OpenAPI scenario relay ] [ Local relay CLI ]

Source:
[ S3 | OpenAPI ]

Connection and selection:
[ endpoint/spec/base URL/operation/bucket/prefix fields ]

Destination:
[ sink ] [ dataset ] [ mode ]

[ Generate config ] [ Copy command ]
```

## Relay within the existing datasource connection flow

The relay modes described above treat relay as a separate import path with its own dedicated UI. An alternative is to integrate relay transparently into the existing datasource connection and export flow, keeping the same UI experience.

In this model a connection carries a `relay_mode` flag. The UI presents the same tabs and cards — connection tab bar, Refine Dataset, Data Preview, Data Masking, Sampling Strategy, Export Destination — regardless of whether the backend can reach the datasource directly or not.

The difference is which component drives the adapter calls:

```
Normal:  browser → backend.preview()  ← backend → datasource
Relay:   browser → datasource (fetch directly)
                → backend /relay/preview  (send fetched records)
                → backend /relay/chunk    (send pages during export)
```

### Connection model change

Add a `relay_mode: bool` flag to existing connection types, or introduce a `browser_relay` connection type with a `source_url` field that only the browser resolves. The connection modal gets one new toggle; no other model changes are needed.

### Two new backend endpoints

| Endpoint | Responsibility |
|---|---|
| `POST /api/v1/connections/{id}/relay/preview` | Browser sends fetched records; backend applies filters, masking, and schema inference; returns the same preview response shape |
| `POST /api/v1/export-jobs/{id}/relay/chunk` | Browser sends a page of records; backend applies masking and sampling and writes to sink |
| `POST /api/v1/export-jobs/{id}/relay/complete` | Browser signals it has finished sending all pages |

These endpoints mirror the server-side adapter contract so masking, sampling, sink writing, and progress tracking stay unchanged.

### JS relay driver

When relay mode is active, `triggerPreview()` and the export button call the source URL directly from the browser, then funnel results through the relay endpoints instead of the normal adapter path. The job polling loop, progress badges, and Jobs view are unchanged.

### CORS requirement

The datasource on LAN-A must allow `OPTIONS`/`GET` from the browser's origin. For ClickHouse this is configurable via response headers; for Trino it may require a reverse-proxy header. The connection modal should surface this requirement and run a CORS check alongside the normal ping test when relay mode is enabled.

## When not to use it

Do not use client-side relay when:

- The browser cannot reach both sides at the same time.
- Policy forbids data from passing through the user's workstation.
- Transfers must run unattended or on a schedule.
- The source S3 bucket, S3-compatible service, or custom API cannot be made CORS-compatible and no helper application is allowed.
- Transfers are so large that browser-based retries and local network stability are not acceptable.

In those cases, prefer the local relay SDK/CLI if policy allows installing and running a user-side tool.

## Design review notes

Findings from a cross-file correctness review of this document. Ranked most-severe first.

### 1. AWS SDK v3 contradicts no-bundler constraint (line 154)

The document recommends AWS SDK for JavaScript v3 (`S3Client`, `ListObjectsV2Command`, `GetObjectCommand`). The project constraint is "No framework, no build step, no bundler; all JS in `browser.js`." AWS SDK v3 is distributed as ESM/CJS npm packages with no standalone CDN-ready browser build. Using it requires introducing a bundler (violating the constraint) or falling back to the v2 SDK via CDN (different API surface). The browser-side S3 client approach needs to be re-evaluated — either the no-bundler constraint is relaxed for relay, or the implementation uses the v2 CDN SDK, or S3 operations are proxied through the backend.

### 2. relay/chunk assumes push-driven worker; worker is an autonomous pull loop (line 574)

The current ARQ worker calls `adapter.count()` and `adapter.fetch_page()` in its own batch loop and is not designed to receive externally pushed record pages. Implementing `relay/chunk` requires a new job state machine — a `relay_pending` status, an incoming record buffer, a per-chunk HTTP handler — not a thin endpoint on top of the existing worker. Without this, a naive `relay/chunk` handler would write records to the sink while the worker simultaneously tries to pull from the unreachable datasource, producing duplicate or corrupt output.

### 3. Two incompatible API designs are described without reconciliation (lines 117 and 569)

The API shape section (lines 117–123) defines a dedicated `/api/v1/client-relay/sessions/{id}` resource family. The same-flow section (lines 569–575) defines a completely different set of endpoints grafted onto existing resources: `/api/v1/connections/{id}/relay/preview` and `/api/v1/export-jobs/{id}/relay/chunk`. Neither section marks itself as preferred or as an alternative to the other. This extends to two unreconciled finalisation endpoints: `POST /client-relay/sessions/{id}/complete` (line 121) and `POST /export-jobs/{id}/relay/complete` (line 575). One design must be chosen before implementation begins.

### 4. relay_mode scope is understated — ConnectionCreate has no such field (line 567)

The document says adding relay support requires "one new toggle; no other model changes are needed." `ConnectionCreate` in `models.py` has no `relay_mode` field. Adding it requires a new Pydantic field on `ConnectionCreate` and `ConnectionResponse`, a new DB column, and an Alembic migration. Without these, the field is silently dropped at the Pydantic validation boundary and never persisted.

### 5. relay/preview claims masking is applied; masking only exists in the export worker (line 573)

`apply_masking()` is called only in `export/worker.py`. No preview route calls it. A developer implementing `relay/preview` who expects to reuse existing preview-path masking will find no such code and may ship a relay/preview endpoint that exposes sensitive fields even when masking rules are configured. This is a new code path, not a reuse of existing infrastructure.

### 6. Section heading says "Two new backend endpoints" but the table lists three (line 569)

The heading immediately precedes a table with three rows: `relay/preview`, `relay/chunk`, and `relay/complete`. An implementer reading the heading as authoritative will build two endpoints and miss `relay/complete`, leaving relay export jobs stuck in an in-progress state permanently.

### 7. Same-flow masking claim contradicts browser implementation notes (lines 573 and 171)

Line 171 states: "This mode should relay raw objects or object chunks. It should not try to reproduce the full server-side S3 adapter behaviour in the browser, such as … server-side sampling." The same-flow section's `relay/chunk` table entry says "backend applies masking and sampling." The contradiction leaves implementers with no authoritative answer on whether sampling runs during relay exports.

### 8. "DNS and TCP reachability" listed as a browser pre-flight check; browsers have no raw DNS or TCP API (line 99)

The Fetch API collapses DNS failure, TCP refusal, and CORS rejection into a single generic `TypeError` with no programmatic distinction. The pre-flight diagnostic UI cannot surface the specific error type described. The check should be scoped to "HTTP reachability" and the error message should acknowledge the browser's limited diagnostic visibility.

### 9. CORS example uses MinIO array format; AWS SDK PutBucketCors requires `{"CORSRules": [...]}` (line 191)

The example shows a bare JSON array `[{...}]`, which is the MinIO `mc`/SDK format. The AWS SDK `PutBucketCors` API requires the object form `{"CORSRules": [{...}]}`. The comment labels it "S3/MinIO" without distinguishing. A reader configuring an AWS S3 bucket from this example will receive a 400 error with no indication whether the spec or their tooling is wrong.

### 10. fetch() with ReadableStream body is unsupported in Safari (line 165)

Using a `ReadableStream` as the body of a `fetch()` PUT requires the non-standard `duplex: 'half'` option in Chromium and is not supported in Safari as of 2024. Chunks will be silently buffered or the call will fail in Safari, negating the memory-efficiency goal for large objects. A compatibility note or a fallback to `ArrayBuffer` chunks is needed.
