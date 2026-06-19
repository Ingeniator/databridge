from __future__ import annotations

import json
import re

import httpx
import structlog

logger = structlog.get_logger(__name__)

_TIMEOUT = 10.0

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def render_payload(template: str | None, context: dict) -> dict:
    """Render a JSON template string with {{variable}} placeholders.

    Falls back to ``context`` as-is when no template is provided.
    Unknown placeholders are left as empty strings.
    """
    if not template:
        return context
    rendered = _PLACEHOLDER.sub(lambda m: str(context.get(m.group(1), "")), template)
    try:
        return json.loads(rendered)
    except json.JSONDecodeError:
        logger.warning("webhook_template_invalid_json", rendered=rendered)
        return context


async def deliver_webhook(url: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            logger.info("webhook_delivered", url=url, status=r.status_code)
    except Exception as exc:
        logger.warning("webhook_delivery_failed", url=url, error=str(exc))
