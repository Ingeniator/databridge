# Metrics & Health

## One-liner
Prometheus metrics via a custom middleware with path normalization, multiprocess-safe `/metrics` endpoint, and three probe endpoints (`/livez`, `/ready`, `/health`) using a three-state component model.

## Problem
k8s needs separate signals for "is the process alive" (liveness) and "is it ready to serve traffic" (readiness). Ops teams need a richer endpoint that names which backend is degraded without exposing credentials. Dynamic path segments (UUIDs, numeric IDs) in metric labels cause unbounded cardinality. Multi-worker deployments need metrics merged across processes.

---

## 1. Prometheus Middleware

`PrometheusMiddleware` in `src/<service>/middlewares/metrics_middleware.py` — see `yallmp/app/middlewares/metrics_middleware.py` as the reference.

### Metrics

```python
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    ["method", "endpoint", "status_code"],
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Histogram of request processing time",
    ["method", "endpoint"],
)
```

Add a `group_id` label only if the service uses per-group billing or routing.

### Path normalization

Dynamic path segments must be collapsed to `/:id` before recording the `endpoint` label — otherwise every UUID or record ID creates a new time series.

```python
_PATH_ID_RE = re.compile(r'/[0-9a-f]{8,}(?:-[0-9a-f]{4,}){0,4}|/\d+')

def _normalize_path(path: str) -> str:
    return _PATH_ID_RE.sub("/:id", path)
```

### Streaming requests

Skip metrics recording for streaming / chunked requests to avoid buffering issues:

```python
is_stream = (
    request.method == "POST"
    and (
        content_type.startswith("multipart/form-data")
        or "chunked" in transfer_encoding
    )
)
if is_stream:
    return await call_next(request)
```

---

## 2. Multiprocess-safe `/metrics` endpoint

When running under multiple workers (gunicorn/uvicorn multiprocess), each worker writes its own Prometheus state to `PROMETHEUS_MULTIPROC_DIR`. The `/metrics` endpoint must merge all workers' data using `MultiProcessCollector`.

```python
def get_metrics_registry() -> CollectorRegistry:
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if multiproc_dir and os.path.isdir(multiproc_dir):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return registry
    return REGISTRY

@app.get("/metrics")
async def metrics_endpoint():
    return Response(
        content=generate_latest(get_metrics_registry()),
        media_type=CONTENT_TYPE_LATEST,
    )
```

`PROMETHEUS_MULTIPROC_DIR` must be created before any metrics objects are instantiated — do it at module level in `main.py` before importing metrics.

---

## 3. Probe Endpoints

### `/livez` — liveness

Always returns 200. Never checks dependencies. Used by k8s to restart crashed processes.

```python
@app.get("/livez")
async def livez():
    return {"status": "ok"}
```

### `/ready` — readiness

Checks all **enabled** components. Returns 200 if every enabled component is `"ok"`, 503 if any is `"degraded"`. k8s withholds traffic until 200 is returned.

```json
{
  "status": "degraded",
  "components": {
    "clickhouse": "ok",
    "s3": "degraded",
    "redis": "disabled"
  }
}
```

### `/health` — full health

Same component sweep as `/ready` plus a `details` dict with error messages and a `version` field. For dashboards and monitoring — not used by k8s probes directly.

```json
{
  "status": "degraded",
  "version": "1.2.3",
  "components": {
    "clickhouse": "ok",
    "s3": "degraded",
    "redis": "disabled"
  },
  "details": {
    "s3": "Connection refused (bucket: my-bucket)"
  }
}
```

`details` is omitted (or `null`) when all components are healthy.

### Component state model

| State | Meaning | Counted in `/ready`? |
|---|---|---|
| `"ok"` | Component reachable and healthy | Yes — must all be ok for 200 |
| `"degraded"` | Component unreachable or failing | Yes — causes 503 |
| `"disabled"` | Component not configured / feature-flagged off | No — excluded from status |

`disabled` components must not be pinged and must not influence the overall `"ok"`/`"degraded"` status. This lets services start up with only a subset of backends configured.

---

## 4. Probe Implementation Pattern

Both `/ready` and `/health` run component checks concurrently via `asyncio.gather`. Each check is a coroutine that returns `(name, state, detail | None)`.

```python
async def _ping_component(name: str, ping_fn) -> tuple[str, str, str | None]:
    try:
        await ping_fn()
        return name, "ok", None
    except Exception as exc:
        return name, "degraded", str(exc)
```

Do not use `exc_info=True` in health checks — the error string in `details` is sufficient and probes are called frequently.

---

## 5. Startup / Shutdown Logging

Log at INFO level on process start and stop using FastAPI's lifespan context manager. This anchors the timeline in log aggregators.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("service_startup", version=settings.version)
    # … initialise resources …
    yield
    # … release resources …
    logger.info("service_shutdown")

app = FastAPI(lifespan=lifespan)
```

---

## 6. Middleware Registration Order

Middleware executes in reverse registration order in Starlette/FastAPI. Register in this order so execution is:
`RequestID → Logging → Prometheus → route handler`

```python
app.add_middleware(PrometheusMiddleware)   # innermost — closest to handler
app.add_middleware(LoggingMiddleware)
app.add_middleware(RequestIDMiddleware)    # outermost — runs first
```

Probe paths (`/livez`, `/ready`, `/health`, `/metrics`) pass through `PrometheusMiddleware` but are silenced from `uvicorn.access` logs via `SilenceProbesFilter` (see [Logging, Audit, and Exception Handling](service-logging-audit-and-exceptions.md)).

---

## 7. Scope

**In:** `http_requests_total` and `http_request_duration_seconds` metrics; path normalization; multiprocess-safe `/metrics`; three probe endpoints with three-state component model; startup/shutdown lifecycle logging.

**Out:** Business-domain metrics (import bytes, job counts — defined per service); distributed tracing (OpenTelemetry); alerting rules; Grafana dashboard definitions.
