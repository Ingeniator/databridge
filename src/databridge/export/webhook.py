from __future__ import annotations

import structlog
import httpx

logger = structlog.get_logger(__name__)

_TIMEOUT = 10.0


async def deliver_webhook(url: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            logger.info("webhook_delivered", url=url, status=r.status_code)
    except Exception as exc:
        logger.warning("webhook_delivery_failed", url=url, error=str(exc))
