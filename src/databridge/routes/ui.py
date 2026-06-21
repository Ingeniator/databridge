from __future__ import annotations

import hashlib
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


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


@router.get("/api/v1/ui-config", response_model=UiConfigResponse)
async def ui_config() -> UiConfigResponse:
    settings = get_settings()
    return UiConfigResponse(
        connection_types=CONNECTION_TYPES,
        hide_auth_inputs=settings.server.hide_auth_inputs,
        webhook_allowed_url_prefixes=list(settings.export.webhook_allowed_url_prefixes),
    )


@router.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    settings = get_settings()
    root_path = settings.server.root_path.rstrip("/")
    js_v = _file_hash(_STATIC_DIR / "browser.js")
    css_v = _file_hash(_STATIC_DIR / "browser.css")
    html = (_TEMPLATES_DIR / "browser.html").read_text()
    html = html.replace('data-base=""', f'data-base="{root_path}"')
    html = html.replace('src="/static/browser.js"', f'src="{root_path}/static/browser.js?v={js_v}"')
    html = html.replace('href="/static/browser.css"', f'href="{root_path}/static/browser.css?v={css_v}"')
    return HTMLResponse(content=html)
