from __future__ import annotations

import base64
import re
from typing import NamedTuple

import structlog
from fastapi import HTTPException, Request

from databridge.config import get_settings

logger = structlog.get_logger(__name__)

_UNSAFE = re.compile(r"[^\w/\-@.]")


def _sanitize(key: str) -> str:
    if ".." in key:
        return ""  # reject path traversal attempts entirely
    return _UNSAFE.sub("", key).strip()


class AuthContext(NamedTuple):
    public_key: str
    is_org_admin: bool = False


async def get_auth(request: Request) -> AuthContext:
    path = request.url.path

    # nginx upstream header (primary)
    if group_id := request.headers.get("X-Group-ID"):
        key = _sanitize(group_id)
        if not key:
            logger.warning("auth_rejected", reason="empty key after sanitisation", path=path)
            raise HTTPException(status_code=401, detail="authentication required")
        is_admin = request.headers.get("X-Role", "").upper() == "ORG_ADMIN"
        logger.info("authenticated", public_key=key, source="header")
        return AuthContext(public_key=key, is_org_admin=is_admin)

    # Basic auth fallback
    if auth_header := request.headers.get("Authorization", ""):
        scheme, _, encoded = auth_header.partition(" ")
        if scheme.lower() == "basic" and encoded:
            try:
                decoded = base64.b64decode(encoded).decode()
                public_key, _, _ = decoded.partition(":")
                key = _sanitize(public_key)
                if key:
                    logger.info("authenticated", public_key=key, source="basic")
                    return AuthContext(public_key=key)
            except Exception:
                pass

    if get_settings().server.debug:
        logger.warning("auth_dev_fallback", path=path)
        return AuthContext(public_key="dev", is_org_admin=True)

    logger.warning("auth_rejected", reason="no valid credentials", path=path)
    raise HTTPException(status_code=401, detail="authentication required")
