from __future__ import annotations

import os
import re
import time

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    multiprocess,
    CONTENT_TYPE_LATEST,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_PATH_ID_RE = re.compile(r"/[0-9a-f]{8,}(?:-[0-9a-f]{4,}){0,4}|/\d+")

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
)


def _normalize_path(path: str) -> str:
    return _PATH_ID_RE.sub("/:id", path)


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_type = request.headers.get("content-type", "")
        transfer_enc = request.headers.get("transfer-encoding", "")
        is_stream = request.method == "POST" and (
            content_type.startswith("multipart/form-data")
            or "chunked" in transfer_enc
        )
        if is_stream:
            return await call_next(request)

        endpoint = _normalize_path(request.url.path)
        t0 = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - t0

        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=endpoint,
            status_code=str(response.status_code),
        ).inc()
        REQUEST_DURATION.labels(method=request.method, endpoint=endpoint).observe(duration)
        return response


def get_metrics_registry() -> CollectorRegistry:
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if multiproc_dir and os.path.isdir(multiproc_dir):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return registry
    return REGISTRY


async def metrics_endpoint(_request: Request) -> Response:
    return Response(
        content=generate_latest(get_metrics_registry()),
        media_type=CONTENT_TYPE_LATEST,
    )
