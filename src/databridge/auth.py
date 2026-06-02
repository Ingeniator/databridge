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


def _map_role(x_role: str) -> tuple[str, bool]:
    """Return (role, is_org_admin) from X-Role header value."""
    upper = x_role.upper()
    if upper == "SUPER_ADMIN":
        return "super_admin", True
    if upper == "ORG_ADMIN":
        return "org_admin", True
    return "user", False


class AuthContext(NamedTuple):
    public_key: str          # "org_id/user_id" — backwards compat for connection queries
    is_org_admin: bool = False
    org_id: str = ""
    user_id: str = ""
    role: str = "user"       # "super_admin" | "org_admin" | "user"


async def get_auth(request: Request) -> AuthContext:
    path = request.url.path

    # nginx upstream header (primary)
    if group_id := request.headers.get("X-Group-ID"):
        key = _sanitize(group_id)
        if not key:
            logger.warning("auth_rejected", reason="empty key after sanitisation", path=path)
            raise HTTPException(status_code=401, detail="authentication required")
        # Split org_id/user_id on first "/"
        if "/" in key:
            org_id, user_id = key.split("/", 1)
        else:
            org_id, user_id = key, ""
        role, is_org_admin = _map_role(request.headers.get("X-Role", ""))
        logger.info("authenticated", public_key=key, source="header")
        return AuthContext(
            public_key=key,
            is_org_admin=is_org_admin,
            org_id=org_id,
            user_id=user_id,
            role=role,
        )

    # Basic auth fallback
    if auth_header := request.headers.get("Authorization", ""):
        scheme, _, encoded = auth_header.partition(" ")
        if scheme.lower() == "basic" and encoded:
            try:
                decoded = base64.b64decode(encoded).decode()
                public_key, _, _ = decoded.partition(":")
                key = _sanitize(public_key)
                if key:
                    if "/" in key:
                        org_id, user_id = key.split("/", 1)
                    else:
                        org_id, user_id = key, ""
                    logger.info("authenticated", public_key=key, source="basic")
                    return AuthContext(
                        public_key=key,
                        is_org_admin=False,
                        org_id=org_id,
                        user_id=user_id,
                        role="user",
                    )
            except Exception:
                pass

    if get_settings().server.debug:
        logger.warning("auth_dev_fallback", path=path)
        return AuthContext(
            public_key="dev",
            is_org_admin=True,
            org_id="dev",
            user_id="dev",
            role="super_admin",
        )

    logger.warning("auth_rejected", reason="no valid credentials", path=path)
    raise HTTPException(status_code=401, detail="authentication required")
