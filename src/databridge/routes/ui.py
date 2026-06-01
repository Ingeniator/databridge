from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from databridge.config import get_settings
from databridge.models import UiConfigResponse

router = APIRouter(tags=["ui"])

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

CONNECTION_TYPES = ["s3", "clickhouse", "trino", "langfuse", "dataset"]


@router.get("/api/v1/ui-config", response_model=UiConfigResponse)
async def ui_config() -> UiConfigResponse:
    settings = get_settings()
    return UiConfigResponse(
        connection_types=CONNECTION_TYPES,
        hide_auth_inputs=settings.server.hide_auth_inputs,
    )


@router.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = (_TEMPLATES_DIR / "browser.html").read_text()
    return HTMLResponse(content=html)
