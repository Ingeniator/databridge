from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from databridge.config import get_settings
from databridge.db.pool import create_pool
from databridge.logging_config import setup_logging
from databridge.metrics import PrometheusMiddleware, metrics_endpoint
from databridge.routes.health import router as health_router

logger = structlog.get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(debug=settings.server.debug, silence_probes=settings.server.silence_probes)
    logger.info("service_startup", version="0.1.0")
    app.state.pool = await create_pool()
    yield
    await app.state.pool.close()
    logger.info("service_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="databridge",
        root_path=settings.server.root_path,
        lifespan=lifespan,
    )

    # Middleware registration order (Starlette: last registered = outermost)
    app.add_middleware(PrometheusMiddleware)   # innermost — closest to handler
    app.add_middleware(RequestIDMiddleware)    # outermost — runs first

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error("unhandled_exception", path=request.url.path,
                     method=request.method, exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    from databridge.routes.connections import router as connections_router
    from databridge.routes.ui import router as ui_router, _STATIC_DIR
    from fastapi.staticfiles import StaticFiles

    app.include_router(health_router)
    app.include_router(connections_router)
    app.include_router(ui_router)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.add_route("/metrics", metrics_endpoint)

    return app


app = create_app()
