# Logging, Audit, and Exception Handling

## One-liner
JSON structured logging via structlog, a global FastAPI exception handler, audit events for key operations, and consistent exception capture patterns across HTTP handlers and background workers.

## Problem
Plain text logs are hard to query in production log aggregators (Loki, Datadog, CloudWatch). Unhandled exceptions produce silent 500s with no log context. Background worker errors surface only as arq task failures without structured fields. Per-file/per-record exceptions swallow tracebacks (only `str(e)` is kept). Key operations — job enqueue, job complete, file upload — have no audit trail. Health-check noise from k8s probes pollutes log streams.

---

## 1. Structured Logging Setup

### `src/<service>/logging_config.py`

`setup_logging(debug, silence_probes)` called once at process startup (both API and worker processes share the same call).

**Processor chain:**
```
merge_contextvars → filter_by_level → add_logger_name → add_log_level
→ PositionalArgumentsFormatter → TimeStamper(fmt="iso") → StackInfoRenderer
→ format_exc_info → UnicodeDecoder → JSONRenderer (prod) / ConsoleRenderer (debug)
```

- `format_exc_info` serialises exception tracebacks into the `exception` JSON field — call sites pass `exc_info=True`, not the exception string.
- `silence_probes=True` (default): `SilenceProbesFilter` on `uvicorn.access` suppresses log lines for `/livez`, `/ready`, `/health`, `/metrics`.
- `debug=True`: `ConsoleRenderer` (coloured) + `DEBUG` level.

**Call sites must use `exc_info=True`, never `error=str(e)`:**
```python
# wrong — loses traceback
logger.error("import_failed", error=str(e))

# correct — traceback ends up in the "exception" JSON field
logger.error("import_failed", exc_info=True)
```

### Context propagation
`structlog.contextvars` carries bound fields across async boundaries within a single request or job execution. Bound fields flow automatically into every `logger.*` call without being passed manually.

---

## 2. Request ID Middleware

`RequestIDMiddleware` in `main.py` runs before every request:

1. Read `x-request-id` header. If absent, **generate** a UUID4 — never leave it empty.
2. `structlog.contextvars.clear_contextvars()` — prevents leak across requests.
3. `structlog.contextvars.bind_contextvars(request_id=request_id)`.
4. Return the same `request_id` in the `x-request-id` response header so callers can correlate.

```python
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response
```

---

## 3. Global Exception Handler

Added to the FastAPI app so every unhandled exception produces a structured log line and a consistent JSON error response — never a raw 500 with no log.

```python
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
```

`HTTPException` is **not** caught here — FastAPI handles it normally and produces the correct 4xx response. If you want to log 4xx as well, add a separate `@app.exception_handler(HTTPException)` handler that logs at `warning` level and re-raises or returns the standard response.

**Error response shape** — all error responses (4xx and 5xx) return:
```json
{ "detail": "<human-readable message>" }
```
FastAPI's default `HTTPException` handler already uses this shape. Custom handlers must match it.

---

## 4. Audit Logging

Audit events record the outcome of operations that mutate state or cross a trust boundary. They are structured log lines at `INFO` level with an `event` field that is a stable snake_case identifier.

### Required audit events

| Event name | When | Required fields |
|---|---|---|
| `job_enqueued` | HTTP handler accepts a job and enqueues it | `job_id`, `job_type`, `datasource`, `target`, `dataset_name` |
| `job_started` | Worker picks up a job | `job_id`, `job_type`, `datasource`, `target` |
| `job_completed` | Worker finishes successfully | `job_id`, `files_uploaded`, `files_failed`, `bytes_total`, `duration_ms` |
| `job_failed` | Worker raises an unhandled exception | `job_id`, `exc_info=True` |
| `file_uploaded` | A single file/record is successfully uploaded to the target | `job_id`, `key` or `filename`, `bytes` |
| `file_upload_failed` | A single file fails (non-fatal, job continues) | `job_id`, `key`, `exc_info=True` |
| `authenticated` | Credential check passes | `public_key`, `source` (`header`/`basic`) |
| `auth_rejected` | Credential check fails | `reason`, `path` |
| `dataset_created` | Dataset object created in target system | `job_id`, `dataset_id`, `dataset_name`, `target` |

Audit events must not contain secret values (tokens, passwords, full credentials).

---

## 5. Worker / Background Job Exception Handling

Workers (arq, Celery, etc.) do not have FastAPI's exception handler. They need their own pattern.

### Pattern: wrap the entire job body
```python
async def import_dataset(ctx: dict, *, job_id: str, ...) -> dict:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(job_id=job_id)
    logger.info("job_started", job_type="import_dataset")
    t0 = time.monotonic()
    try:
        result = await _do_import(ctx, ...)
        logger.info(
            "job_completed",
            duration_ms=round((time.monotonic() - t0) * 1000),
            **result,
        )
        return result
    except Exception:
        logger.error("job_failed", duration_ms=round((time.monotonic() - t0) * 1000), exc_info=True)
        raise  # let arq mark the job as failed
```

- Bind `job_id` to contextvars at the start so all nested log calls carry it.
- Always re-raise after logging — the queue framework needs to see the exception to mark the job state correctly.
- Per-item failures inside the job body (e.g., per-file upload errors) are `warning` + continue; job-level failures are `error` + re-raise.

---

## 6. Credential Redaction

Sensitive header values must be partially masked before any log line is written. The implementation lives in `src/<service>/security.py` and is shared with the logging middleware — see `yallmp/app/core/security.py` as the reference.

### `redact_headers(headers: dict) -> dict`

Returns a **copy** of the headers dict with sensitive values replaced. The original dict is never mutated.

**Sensitive header names** (matched case-insensitively, full match):
```python
_SENSITIVE_HEADER_PATTERNS = [
    "authorization",
    "x-api-key",
    "x-token",
    "cookie",
    "set-cookie",
    "proxy-authorization",
]
```

**Masking rule** — keep the first 4 characters for identification, replace the rest:
```python
_REDACT_PREFIX_LEN = 4

def _redact_value(value: str) -> str:
    if len(value) <= _REDACT_PREFIX_LEN:
        return "[REDACTED]"
    return value[:_REDACT_PREFIX_LEN] + "...[REDACTED]"
```

Example: `"Bearer eyJhbGci..."` → `"Bear...[REDACTED]"`.

### Usage in the logging middleware

Apply `redact_headers` to `request.headers` before any `logger.*` call that includes header data:

```python
headers = redact_headers(dict(request.headers))
logger.debug("request_received", method=request.method, path=request.url.path, headers=headers)
```

### What this does NOT cover

- Query-string parameters (e.g., `?api_key=...`) — scrub manually at the call site if the endpoint accepts secrets via query params.
- Request/response body fields — do not log raw bodies that may contain credentials; log only structural metadata (size, content-type).
- Structured log fields set explicitly by call sites (e.g., `logger.info("authenticated", public_key=public_key)`) — these are safe by design because they carry only the non-secret identifier.

---

## 7. Scope

**In:** structlog JSON output; ISO timestamps; traceback serialisation via `exc_info=True`; probe silencing; debug mode; request ID generation and propagation; global FastAPI exception handler; consistent error response shape; audit events for state-mutating operations; worker job lifecycle logging.

**Out:** Log sampling / rate limiting; per-logger level overrides at runtime; log shipping configuration (Loki, Datadog, etc.); distributed tracing (OpenTelemetry spans); PII scrubbing processors.
